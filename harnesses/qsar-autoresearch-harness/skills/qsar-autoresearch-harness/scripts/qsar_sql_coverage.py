"""Bounded native SQL scan facts, not scientific coverage acceptance.

Native SQL is parsed as data, never executed. The fixed SQLite schema retains
all fields; check_profile establishes index/profile consistency only. Independent
native scan review and scientific joins/resolutions remain separate obligations.
"""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

import qsar_native_sources as native


class SQLCoverageError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_binding(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
            size += len(block)
    return {'path': str(path), 'sha256': h.hexdigest(), 'bytes': size}


def code_bindings():
    return {p.name: file_binding(p) for p in (Path(__file__), Path(native.__file__))}


def obligations(gaps, source_id):
    """Derive historical gap/table identities without accepting a claimed scope."""
    result, seen = [], set()
    for gap in gaps:
        if gap.get('source_id') != source_id:
            continue
        tables = gap.get('unparsed_tables')
        if not isinstance(tables, list) or not tables:
            raise SQLCoverageError('SQL gap has no table obligations')
        gap_hash = digest(gap)
        for table in tables:
            if not isinstance(table, str) or not table:
                raise SQLCoverageError('Invalid obligation table')
            identity = {'original_gap_sha256': gap_hash, 'table_name': table}
            key = digest(identity)
            if key in seen:
                raise SQLCoverageError('Duplicate SQL obligation')
            seen.add(key)
            result.append(dict(identity, source_id=source_id, obligation_id=key))
    return result


IDENT = r'`([^`]+)`'
CREATE = re.compile(r'\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' + IDENT + r'\s*\(', re.I)
INSERT = re.compile(r'\s*INSERT\s+INTO\s+' + IDENT + r'\s*(\([^)]*\))?\s*VALUES\s*(.*);\s*\Z', re.I | re.S)
COMMENT = re.compile(r'/\*!(?:\d+)?\s*(.*?)\*/', re.S)


class CheckedLines:
    """Resource bound is a hard failure, never a truncated successful scan.

Executable comments are conservatively restricted to one-line dump controls.
This may reject exotic valid SQL (including comment-like string content); it
cannot silently accept an unsupported operation hidden in a discarded comment.
"""
    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0
        self.bytes_since_statement = 0
        self.line_number = 0
        self.mode_restored = False
        self.sql_mode_controls = []

    def set_control(self, control, raw, line):
        compact = re.sub(r'\s+', '', control).upper()
        if 'SQL_MODE' in compact:
            if compact in ("SET@OLD_SQL_MODE=@@SQL_MODE,SQL_MODE='NO_AUTO_VALUE_ON_ZERO'",
                           "SETSQL_MODE='NO_AUTO_VALUE_ON_ZERO'"):
                self.mode_restored = False
            elif compact == 'SETSQL_MODE=@OLD_SQL_MODE':
                self.mode_restored = True
            else:
                raise SQLCoverageError('Unsupported SQL_MODE control')
            self.sql_mode_controls.append({'line': line, 'raw': raw, 'control': control,
                                           'state': 'unknown_restored' if self.mode_restored else 'NO_AUTO_VALUE_ON_ZERO'})
            return True
        variables = ('CHARACTER_SET_CLIENT', 'CHARACTER_SET_RESULTS', 'COLLATION_CONNECTION',
                     'TIME_ZONE', 'UNIQUE_CHECKS', 'FOREIGN_KEY_CHECKS', 'SQL_NOTES')
        for variable in variables:
            if compact in (f'SET@OLD_{variable}=@@{variable}', f'SET{variable}=@OLD_{variable}'):
                return True
        return compact in {
            'SET@SAVED_CS_CLIENT=@@CHARACTER_SET_CLIENT',
            'SETCHARACTER_SET_CLIENT=@SAVED_CS_CLIENT',
            'SETNAMESUTF8MB4', 'SETNAMESUTF8',
            'SETCHARACTER_SET_CLIENT=UTF8MB4', 'SETCHARACTER_SET_CLIENT=UTF8',
            "SETTIME_ZONE='+00:00'",
            'SET@OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS,UNIQUE_CHECKS=0',
            'SET@OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS,FOREIGN_KEY_CHECKS=0',
            'SET@OLD_SQL_NOTES=@@SQL_NOTES,SQL_NOTES=0'}

    def __iter__(self):
        while True:
            line = self.stream.readline(8 * 1024 * 1024 + 1)
            if not line:
                return
            self.line_number += 1
            if len(line) > 8 * 1024 * 1024:
                raise SQLCoverageError('Native line exceeds bounded parser capacity')
            self.bytes_read += len(line.encode('utf-8'))
            self.bytes_since_statement += len(line.encode('utf-8'))
            if self.bytes_since_statement > 16 * 1024 * 1024:
                raise SQLCoverageError('Native statement exceeds bounded parser capacity')
            if re.search(r'/\*[mM]!', line):
                raise SQLCoverageError('Unsupported MariaDB executable comment')
            if '/*!' in line:
                matches = list(COMMENT.finditer(line))
                if len(matches) != line.count('/*!'):
                    raise SQLCoverageError('Multiline executable comment unsupported')
                for match in matches:
                    control = match[1].strip()
                    if 'SQL_MODE' in control.upper() and not re.fullmatch(r'\s*' + re.escape(match[0]) + r'\s*;\s*', line):
                        raise SQLCoverageError('SQL_MODE comment must be a standalone control line')
                    allowed = ((re.match(r'SET\b', control, re.I) and self.set_control(control, match[0], self.line_number)) or
                               re.fullmatch(r'ALTER\s+TABLE\s+`[^`]+`\s+(?:ENABLE|DISABLE)\s+KEYS', control, re.I) or
                               re.fullmatch(r'IF NOT EXISTS|DEFAULT CHARACTER SET \w+ COLLATE \w+|DEFAULT ENCRYPTION=\'[YN]\'', control, re.I))
                    if not allowed:
                        raise SQLCoverageError('Unsupported executable comment: ' + control[:80])
            yield line


def _schema(statement, classified, line):
    match = CREATE.match(classified)
    if not match:
        raise SQLCoverageError(f'Unsupported CREATE TABLE at line {line}')
    # Find the actual closing schema parenthesis; a greedy regex can swallow
    # CREATE ... AS SELECT expressions and falsely declare an empty table.
    position, depth = match.end(), 1
    begin = position
    while position < len(classified) and depth:
        char = classified[position]
        if char in ("'", '`'):
            quoted = native.SQL_STRING if char == "'" else re.compile(r'`[^`]*`')
            token = quoted.match(classified, position)
            if not token:
                raise SQLCoverageError('Unterminated CREATE quoted token')
            position = token.end()
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        position += 1
    if depth:
        raise SQLCoverageError('Unterminated CREATE column list')
    suffix = classified[position:]
    if not re.fullmatch(r'\s*(?:ENGINE\s*=\s*(?:InnoDB|MyISAM)\s*)?'
                        r'(?:AUTO_INCREMENT\s*=\s*\d+\s*)?'
                        r'(?:DEFAULT\s+CHARSET\s*=\s*\w+\s*)?'
                        r'(?:COLLATE\s*=\s*\w+\s*)?;\s*', suffix, re.I):
        raise SQLCoverageError('Unsupported CREATE suffix')
    scalar = r"(?:NULL|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|" + native.SQL_STRING.pattern + r')'
    column_type = (r'(?:(?:tinyint|smallint|mediumint|int|integer|bigint|float|double|decimal|numeric)'
                   r'(?:\(\d+(?:\s*,\s*\d+)?\))?|(?:varchar|char)\(\d+\)|'
                   r'tinytext|text|mediumtext|longtext|date|datetime|timestamp)')
    attributes = (r'(?:\s+(?:NOT\s+NULL|NULL|AUTO_INCREMENT|UNSIGNED|ZEROFILL|'
                  r'COLLATE\s+\w+|DEFAULT\s+' + scalar + r'))*')
    column_pattern = re.compile(IDENT + r'\s+' + column_type + attributes + r'\s*', re.I | re.S)
    identifiers = r'`[^`]+`(?:\(\d+\))?(?:\s*,\s*`[^`]+`(?:\(\d+\))?)*'
    key_columns = r'\(\s*' + identifiers + r'\s*\)'
    constraint_pattern = re.compile(
        r'(?:(?:PRIMARY\s+KEY|(?:UNIQUE\s+|FULLTEXT\s+|SPATIAL\s+)?KEY\s+`[^`]+`)\s*' + key_columns + r'(?:\s+USING\s+BTREE)?|'
        r'CONSTRAINT\s+`[^`]+`\s+FOREIGN\s+KEY\s*' + key_columns + r'\s+REFERENCES\s+`[^`]+`\s*' + key_columns + r')\s*', re.I)
    columns = []
    for definition in native.split_sql(classified[begin:position - 1]):
        column = column_pattern.fullmatch(definition)
        if column:
            columns.append(column[1])
        elif not constraint_pattern.fullmatch(definition):
            raise SQLCoverageError(f'Unsupported schema definition at line {line}')
    if not columns or len({name.casefold() for name in columns}) != len(columns):
        raise SQLCoverageError('Missing or duplicate CREATE columns')
    return match[1], {'columns': columns, 'create_line': line,
                      'create_sql': statement, 'schema_sha256': digest({'columns': columns, 'create_sql': statement})}


def _control(statement, tables):
    """Allow inert dump scaffolding only; DROP after CREATE would destroy data."""
    drop = re.fullmatch(r'\s*DROP TABLE IF EXISTS\s+`([^`]+)`\s*;\s*', statement, re.I)
    if drop:
        return drop[1] not in tables
    return bool(re.fullmatch(
        r'\s*(?:USE\s+`[^`]+`|CREATE DATABASE\s+`[^`]+`|'
        r'LOCK TABLES\s+`[^`]+`\s+WRITE|UNLOCK TABLES)\s*;\s*', statement, re.I))


def scan(source, index_path, source_id, progress=None):
    """Scan every table once into a new index. Never overwrite existing output."""
    source, index_path = Path(source).resolve(), Path(index_path).resolve()
    if not isinstance(source_id, str) or not source_id.strip():
        raise SQLCoverageError('source_id required')
    before, code = file_binding(source), code_bindings()
    # Exclusive reservation prevents clobbering either a native source or evidence.
    with index_path.open('xb'):
        pass
    db = sqlite3.connect(index_path)
    tables, hashes = {}, {}
    total = statements = 0
    started = time.monotonic()
    try:
        db.executescript('''
            PRAGMA journal_mode=DELETE;
            PRAGMA cache_size=-8192;
            PRAGMA temp_store=FILE;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
            CREATE TABLE schemas(table_name TEXT PRIMARY KEY, schema_json TEXT NOT NULL);
            CREATE TABLE native_rows(
                table_name TEXT NOT NULL, statement_ordinal INTEGER NOT NULL, line INTEGER NOT NULL,
                tuple_ordinal INTEGER NOT NULL, columns_json TEXT NOT NULL,
                statement_sha256 TEXT NOT NULL, sql_tuple TEXT NOT NULL,
                body_json TEXT NOT NULL, row_sha256 TEXT NOT NULL);
            CREATE UNIQUE INDEX native_locator ON native_rows(table_name,statement_ordinal,tuple_ordinal);
        ''')
        opener = gzip.open if source.suffix.lower() == '.gz' else open
        with opener(source, 'rt', encoding='utf-8', errors='strict', newline='') as stream:
            lines = CheckedLines(stream)
            for line, statement, classified in native.complete_sql_statements(lines):
                lines.bytes_since_statement = 0
                statements += 1
                if re.match(r'\s*SET\b', classified, re.I):
                    if not lines.set_control(classified.strip().removesuffix(';').strip(), statement, line):
                        raise SQLCoverageError('Unsupported SET control')
                    continue
                if lines.mode_restored and re.match(r'\s*(?:CREATE\s+TABLE|INSERT)\b', classified, re.I):
                    raise SQLCoverageError('Native data/schema after unknown SQL_MODE restoration')
                if re.match(r'\s*CREATE\s+TABLE\b', classified, re.I):
                    name, schema = _schema(statement, classified, line)
                    if name in tables:
                        raise SQLCoverageError('Duplicate CREATE table: ' + name)
                    db.execute('INSERT INTO schemas VALUES (?,?)', (name, canonical(schema).decode()))
                    tables[name] = dict(schema, native_rows=0, parsed_rows=0, errors=0)
                    hashes[name] = hashlib.sha256()
                    continue
                match = INSERT.fullmatch(classified)
                if not match:
                    if _control(classified, tables):
                        continue
                    raise SQLCoverageError(f'Unsupported SQL operation at line {line}: {classified[:80]}')
                name = match[1]
                if name not in tables:
                    raise SQLCoverageError('INSERT without CREATE: ' + name)
                schema = tables[name]['columns']
                columns = schema
                if match[2]:
                    columns = []
                    for token in native.split_sql(match[2][1:-1]):
                        identifier = re.fullmatch(IDENT, token)
                        if not identifier:
                            raise SQLCoverageError('Unsupported INSERT identifier')
                        columns.append(identifier[1])
                    if len(set(columns)) != len(columns) or not set(columns).issubset(schema):
                        raise SQLCoverageError('INSERT columns disagree with schema')
                # Raw INSERT is required for exact source tuple lineage. Embedded
                # comments in values are rejected instead of losing native text.
                raw_match = INSERT.fullmatch(statement)
                if not raw_match or raw_match[3] != match[3]:
                    raise SQLCoverageError('Comments embedded in INSERT unsupported')
                statement_hash = hashlib.sha256(statement.encode('utf-8')).hexdigest()
                columns_json = canonical(columns).decode()
                for ordinal, tuple_sql in enumerate(native.split_sql(raw_match[3]), 1):
                    if not tuple_sql.startswith('(') or not tuple_sql.endswith(')'):
                        raise SQLCoverageError('Malformed native tuple')
                    values = [native.sql_value(token) for token in native.split_sql(tuple_sql[1:-1])]
                    if len(values) != len(columns):
                        raise SQLCoverageError('Native tuple arity differs from schema')
                    body = dict.fromkeys(schema)
                    body.update(zip(columns, values))
                    fact = {'table_name': name, 'statement_ordinal': statements, 'line': line, 'tuple_ordinal': ordinal,
                            'columns': columns, 'statement_sha256': statement_hash,
                            'sql_tuple': tuple_sql, 'body': body}
                    row_hash = digest(fact)
                    hashes[name].update(bytes.fromhex(row_hash))
                    db.execute('INSERT INTO native_rows VALUES (?,?,?,?,?,?,?,?,?)',
                               (name, statements, line, ordinal, columns_json, statement_hash,
                                tuple_sql, canonical(body).decode(), row_hash))
                    tables[name]['native_rows'] += 1
                    tables[name]['parsed_rows'] += 1
                    total += 1
                    if total % 5000 == 0:
                        db.commit()
                    if progress and total % 100000 == 0:
                        progress({'rows': total, 'table': name, 'seconds': round(time.monotonic() - started, 2)})
        if not tables:
            raise SQLCoverageError('No native CREATE schemas')
        after = file_binding(source)
        if before != after or code != code_bindings():
            raise SQLCoverageError('Source or scanner code changed during scan')
        for name, table in tables.items():
            table['rows_sha256'] = hashes[name].hexdigest()
        profile = {'format': 'qsar-native-sql-scan-v1', 'source_id': source_id,
                   'source': before, 'source_after': after, 'source_release': 'unknown',
                   'code': code, 'scope': 'all_native_tables_all_rows',
                   'clean_eof': True, 'parser_errors': 0, 'unsupported_operations': 0,
                   'source_bytes_read': before['bytes'], 'decoded_utf8_bytes_read': lines.bytes_read,
                   'statement_count': statements, 'tables': tables, 'total_rows': total,
                   'sql_mode_controls': lines.sql_mode_controls,
                   'independent_native_audit': False, 'scientific_coverage_accepted': False}
        db.execute('INSERT INTO metadata VALUES (?,?)', ('scan', canonical(profile).decode()))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    profile['index'] = file_binding(index_path)
    return profile


def check_profile(profile):
    """Recompute index facts. This is NOT independent index/native authentication."""
    if profile.get('format') != 'qsar-native-sql-scan-v1' or profile.get('scope') != 'all_native_tables_all_rows':
        raise SQLCoverageError('Unsupported scan profile/scope')
    if not isinstance(profile.get('source_id'), str) or not profile['source_id'].strip() or profile.get('source_release') != 'unknown':
        raise SQLCoverageError('Invalid source identity or unsupported release claim')
    for key in ('source_bytes_read', 'decoded_utf8_bytes_read', 'statement_count'):
        if type(profile.get(key)) is not int or profile[key] <= 0:
            raise SQLCoverageError('Invalid positive scan counter: ' + key)
    if profile['source_bytes_read'] != profile['source']['bytes']:
        raise SQLCoverageError('Source byte count differs from binding')
    if profile.get('clean_eof') is not True or any(type(profile.get(k)) is not int or profile[k] != 0 for k in ('parser_errors', 'unsupported_operations')):
        raise SQLCoverageError('Incomplete or errored scan')
    if profile.get('independent_native_audit') is not False or profile.get('scientific_coverage_accepted') is not False:
        raise SQLCoverageError('Scan cannot assert independent or scientific acceptance')
    if profile['source'] != profile['source_after'] or file_binding(profile['source']['path']) != profile['source']:
        raise SQLCoverageError('Source binding mismatch')
    if profile['code'] != code_bindings() or file_binding(profile['index']['path']) != profile['index']:
        raise SQLCoverageError('Code/index binding mismatch')
    uri = Path(profile['index']['path']).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        stored = db.execute('SELECT value_json FROM metadata WHERE key=?', ('scan',)).fetchone()
        expected = {k: v for k, v in profile.items() if k != 'index'}
        if stored is None or json.loads(stored[0]) != expected:
            raise SQLCoverageError('Profile differs from index scan metadata')
        schemas = {name: json.loads(value) for name, value in db.execute('SELECT table_name,schema_json FROM schemas')}
        if set(schemas) != set(profile['tables']):
            raise SQLCoverageError('Omitted/extra native schemas')
        maximum_ordinal = db.execute('SELECT MAX(statement_ordinal) FROM native_rows').fetchone()[0]
        if profile['statement_count'] < len(schemas) or (maximum_ordinal is not None and profile['statement_count'] < maximum_ordinal):
            raise SQLCoverageError('Statement count below index facts')
        if db.execute('SELECT 1 FROM native_rows r LEFT JOIN schemas s ON r.table_name=s.table_name WHERE s.table_name IS NULL LIMIT 1').fetchone():
            raise SQLCoverageError('Rows without schema')
        total = 0
        for name, schema in schemas.items():
            claimed = profile['tables'][name]
            if any(claimed.get(k) != v for k, v in schema.items()):
                raise SQLCoverageError('Schema mismatch')
            if schema['schema_sha256'] != digest({'columns': schema['columns'], 'create_sql': schema['create_sql']}):
                raise SQLCoverageError('Schema digest mismatch')
            count, hasher = 0, hashlib.sha256()
            for statement_ordinal, line, ordinal, cols, statement_hash, tuple_sql, body, row_hash in db.execute(
                    'SELECT statement_ordinal,line,tuple_ordinal,columns_json,statement_sha256,sql_tuple,body_json,row_sha256 FROM native_rows WHERE table_name=? ORDER BY statement_ordinal,tuple_ordinal', (name,)):
                fact = {'table_name': name, 'statement_ordinal': statement_ordinal, 'line': line, 'tuple_ordinal': ordinal,
                        'columns': json.loads(cols), 'statement_sha256': statement_hash,
                        'sql_tuple': tuple_sql, 'body': json.loads(body)}
                if digest(fact) != row_hash:
                    raise SQLCoverageError('Native row fingerprint mismatch')
                hasher.update(bytes.fromhex(row_hash))
                count += 1
            if any(type(claimed.get(k)) is not int or claimed[k] != count for k in ('native_rows', 'parsed_rows')) or type(claimed.get('errors')) is not int or claimed['errors'] != 0 or claimed.get('rows_sha256') != hasher.hexdigest():
                raise SQLCoverageError('Native count/fingerprint mismatch')
            total += count
        if type(profile['total_rows']) is not int or total != profile['total_rows']:
            raise SQLCoverageError('Total row count mismatch')
    return {'consistent': True, 'independent_native_audit': False, 'scientific_coverage_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('index')
    parser.add_argument('profile')
    parser.add_argument('--source-id', required=True)
    args = parser.parse_args()
    with Path(args.profile).open('x', encoding='utf-8') as output:
        profile = scan(args.source, args.index, args.source_id,
                       progress=lambda value: print(json.dumps(value), flush=True))
        json.dump(profile, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write('\n')
    print(json.dumps({'complete': True, 'tables': len(profile['tables']), 'rows': profile['total_rows']}), flush=True)


if __name__ == '__main__':
    main()
