import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts'
sys.path.insert(0, str(SCRIPTS))
import qsar_sql_coverage as q


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def scan(self, sql):
        src = self.root / 'source.sql'
        src.write_text(sql, encoding='utf-8')
        return q.scan(src, self.root / 'index.sqlite', 'native/source')

    def test_all_tables_and_exact_values(self):
        p = self.scan("CREATE TABLE `a` (`id` int, `body` text);\n"
                      "INSERT INTO `a` (`body`,`id`) VALUES\n('한글, x; ''yes''',1.20e+2),\n(NULL,-1);\n"
                      "CREATE TABLE `empty` (`id` int);")
        self.assertEqual(p['tables']['a']['native_rows'], 2)
        self.assertEqual(p['tables']['empty']['native_rows'], 0)
        with closing(sqlite3.connect(p['index']['path'])) as db:
            rows = db.execute('SELECT body_json,line,tuple_ordinal,columns_json FROM native_rows ORDER BY rowid').fetchall()
        self.assertEqual(json.loads(rows[0][0]), {'body': "한글, x; 'yes'", 'id': '1.20e+2'})
        self.assertEqual(rows[0][1:3], (2, 1))
        self.assertEqual(json.loads(rows[0][3]), ['body', 'id'])
        self.assertEqual(p['source_release'], 'unknown')
        q.check_profile(p)

    def test_fail_closed(self):
        bad = ["UPDATE `other` SET x=1;", "/*!50000 REPLACE INTO x VALUES(1) */;",
               "CREATE TABLE `a` (`x` int, `x` text);", "CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(1,2);",
               "CREATE TABLE `a` (`x` int); INSERT INTO `a` (`x`,`x`) VALUES(1,2);",
               "CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(foo());",
               "CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES('unfinished);",
               "CREATE TABLE `a` (`x` int); DROP TABLE `a`;", "/*!50000 TRUNCATE TABLE other */;"]
        for i, sql in enumerate(bad):
            with self.subTest(sql=sql):
                src = self.root / f'{i}.sql'
                src.write_text(sql)
                with self.assertRaises(ValueError):
                    q.scan(src, self.root / f'{i}.sqlite', 's')

    def test_forged_summary(self):
        p = self.scan('CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(1); CREATE TABLE `e` (`x` int);')
        for mutate in (lambda t: t['a'].update(native_rows=0, parsed_rows=0), lambda t: t.pop('e')):
            forged = copy.deepcopy(p)
            mutate(forged['tables'])
            with self.assertRaises(ValueError):
                q.check_profile(forged)

    def test_unicode_scope_independent_obligations(self):
        gap = {'source_id': 's', 'reason': '한글', 'unparsed_tables': ['a', 'b']}
        result = q.obligations([gap], 's')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['original_gap_sha256'], q.digest(gap))
        self.assertEqual(result[0]['obligation_id'], q.digest({'original_gap_sha256': q.digest(gap), 'table_name': 'a'}))
        with self.assertRaises(ValueError):
            q.obligations([gap, gap], 's')

    def test_rehashed_index_metadata_does_not_hide_false_count(self):
        p = self.scan('CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(1);')
        p['tables']['a'].update(native_rows=0, parsed_rows=0)
        with closing(sqlite3.connect(p['index']['path'])) as db:
            db.execute('UPDATE metadata SET value_json=? WHERE key=?',
                       (q.canonical({k: v for k, v in p.items() if k != 'index'}).decode(), 'scan'))
            db.commit()
        p['index'] = q.file_binding(p['index']['path'])
        with self.assertRaises(ValueError):
            q.check_profile(p)

    def test_dump_controls_and_escapes(self):
        p = self.scan("/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;\n"
                      "CREATE DATABASE /*!32312 IF NOT EXISTS*/ `db` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */;\n"
                      "USE `db`; DROP TABLE IF EXISTS `a`; CREATE TABLE `a` (`x` text);\n"
                      "LOCK TABLES `a` WRITE;\n/*!40000 ALTER TABLE `a` DISABLE KEYS */;\n"
                      "INSERT INTO `a` VALUES('back\\\\slash\\nquote\\\''); UNLOCK TABLES;")
        self.assertEqual(p['total_rows'], 1)
        q.check_profile(p)

    def test_same_line_multiple_insert_statements(self):
        p = self.scan('CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(1); INSERT INTO `a` VALUES(1);')
        self.assertEqual(p['total_rows'], 2)
        q.check_profile(p)

    def test_unsupported_sql_modes_fail_before_literal_interpretation(self):
        for i, control in enumerate(("SET SQL_MODE='NO_BACKSLASH_ESCAPES';",
                                     "SET SQL_MODE='ANSI_QUOTES';",
                                     "/*!40101 SET SQL_MODE='NO_BACKSLASH_ESCAPES' */;",
                                     "/*!40101 SET SQL_MODE='ANSI_QUOTES' */;")):
            source = self.root / f'mode-{i}.sql'
            source.write_text(control + "\nCREATE TABLE `a` (`x` text); INSERT INTO `a` VALUES('a\\nb');")
            with self.subTest(control=control), self.assertRaises(ValueError):
                q.scan(source, self.root / f'mode-{i}.sqlite', 's')

    def test_mode_evidence_and_terminal_restoration(self):
        p = self.scan("/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n"
                      "CREATE TABLE `a` (`x` text); INSERT INTO `a` VALUES('a\\nb');\n"
                      "/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;")
        self.assertEqual(len(p['sql_mode_controls']), 2)
        self.assertEqual(p['sql_mode_controls'][0]['line'], 1)
        self.assertIn('/*!40101', p['sql_mode_controls'][0]['raw'])
        q.check_profile(p)

    def test_data_after_unknown_mode_restoration_rejected(self):
        with self.assertRaises(ValueError):
            self.scan("CREATE TABLE `a` (`x` text);\n/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;\nINSERT INTO `a` VALUES('a\\nb');")

    def test_boolean_error_count_rejected_after_rehash(self):
        p = self.scan('CREATE TABLE `a` (`x` int);')
        p['tables']['a']['errors'] = False
        with closing(sqlite3.connect(p['index']['path'])) as db:
            db.execute('UPDATE metadata SET value_json=? WHERE key=?',
                       (q.canonical({k: v for k, v in p.items() if k != 'index'}).decode(), 'scan'))
            db.commit()
        p['index'] = q.file_binding(p['index']['path'])
        with self.assertRaises(ValueError):
            q.check_profile(p)

    def test_rehashed_metadata_cannot_hide_omitted_empty_schema(self):
        p = self.scan('CREATE TABLE `a` (`x` int); CREATE TABLE `empty` (`x` int);')
        p['tables'].pop('empty')
        with closing(sqlite3.connect(p['index']['path'])) as db:
            db.execute('UPDATE metadata SET value_json=? WHERE key=?',
                       (q.canonical({k: v for k, v in p.items() if k != 'index'}).decode(), 'scan'))
            db.commit()
        p['index'] = q.file_binding(p['index']['path'])
        with self.assertRaises(ValueError):
            q.check_profile(p)

    def test_mixed_mode_comment_line_fails_closed(self):
        with self.assertRaises(ValueError):
            self.scan("CREATE TABLE `a` (`x` text);\n/*!40101 SET SQL_MODE=@OLD_SQL_MODE */; INSERT INTO `a` VALUES('a\\nb'); /*!40101 SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;")

    def test_unsupported_create_and_executable_dialects(self):
        cases = ['CREATE TABLE `a` (`x` int) AS SELECT 1;',
                 'CREATE TABLE `a` (`x` int) BOGUS;',
                 'CREATE TABLE `a` (`x` int) AS SELECT (1);',
                 'CREATE TABLE `a` (`x` int BOGUS);',
                 'CREATE TABLE `a` (`x` int, `X` text);',
                 'CREATE TABLE `a` (`x` int); /*M!100100 INSERT INTO `a` VALUES(1) */;']
        for i, sql in enumerate(cases):
            source = self.root / f'unsupported-create-{i}.sql'
            source.write_text(sql)
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                q.scan(source, self.root / f'unsupported-create-{i}.sqlite', 's')

    def test_profile_invariants_survive_coordinated_metadata_rewrite(self):
        p = self.scan('CREATE TABLE `a` (`x` int); INSERT INTO `a` VALUES(1);')
        changes = [('source_bytes_read', 0), ('source_bytes_read', False),
                   ('statement_count', 0), ('statement_count', False), ('statement_count', 1),
                   ('decoded_utf8_bytes_read', 0), ('source_release', 'invented'),
                   ('source_id', ''), ('source_id', '   ')]
        for key, value in changes:
            forged = copy.deepcopy(p)
            forged[key] = value
            with closing(sqlite3.connect(p['index']['path'])) as db:
                db.execute('UPDATE metadata SET value_json=? WHERE key=?',
                           (q.canonical({k: v for k, v in forged.items() if k != 'index'}).decode(), 'scan'))
                db.commit()
            forged['index'] = q.file_binding(p['index']['path'])
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                q.check_profile(forged)


if __name__ == '__main__':
    unittest.main()
