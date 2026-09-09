"""Validate the installed skill using only the Python standard library."""
from pathlib import Path
import json
import sys


def validate(root):
    required = [
        'SKILL.md', 'agents/openai.yaml', 'references/roles.md',
        'references/visual-playbook.md', 'references/trigger-tests.md',
        'assets/layout-atlas.svg',
        'templates/brief.example.json', 'templates/feedback.example.json',
        'scripts/audit_pptx.py', 'scripts/test_audit_pptx.py', 'scripts/requirements.txt',
        'scripts/validate_harness.py', 'scripts/validate_harness.ps1', 'scripts/validate_harness.sh',
    ]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ValueError('Missing files: ' + ', '.join(missing))
    protocol = (root / 'SKILL.md').read_text(encoding='utf-8')
    for text in ['name: classroom-slide-design-harness', 'Message before theme',
                 'Phase 0: Context Check', 'QA Review', 'Repeat and improve', 'Test Scenarios']:
        if text not in protocol:
            raise ValueError('Missing protocol contract: ' + text)
    brief = json.loads((root / 'templates/brief.example.json').read_text(encoding='utf-8'))
    slides = brief['slides']
    if not slides or [s['number'] for s in slides] != list(range(1, len(slides) + 1)):
        raise ValueError('Example slide numbers must be consecutive')
    if any(not isinstance(s['minutes'], (int, float)) or s['minutes'] <= 0 for s in slides):
        raise ValueError('Example speaking times must be positive')
    if abs(sum(s['minutes'] for s in slides) - brief['explanation_minutes']) > 0.001:
        raise ValueError('Example speaking time does not match total')
    for slide in slides:
        if any(not slide.get(k) for k in ('message', 'action', 'layout')):
            raise ValueError('Every example slide needs a message, action and layout')
    if len({s['layout'] for s in slides}) < min(6, len(slides)):
        raise ValueError('Example needs content-led layout variety')
    feedback = json.loads((root / 'templates/feedback.example.json').read_text(encoding='utf-8'))
    if feedback['reviewer_verdict'] != 'not_reviewed':
        raise ValueError('Example feedback must not imply an actual review')


if __name__ == '__main__':
    try:
        validate(Path(__file__).resolve().parents[1])
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f'Classroom slide harness validation failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
    print('Classroom slide design harness structure OK.')
