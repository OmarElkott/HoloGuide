"""Inspect GitHub hygiene and add missing labels using the existing Git credential.

Never prints/stores credentials. Does not alter protections without admin access;
existing rules are inspected and retained. Owner settings are in the P4 handoff.
"""
import argparse
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

REPOSITORY = 'OmarElkott/HoloGuide'
LABELS = {
    **{name: ('1d76db', 'Person 4 roadmap task') for name in
       ('DSH-12', 'DSH-07', 'DSH-10', 'EVL-01b', 'EVL-05', 'GIT-01')},
    **{f'area:{name}': ('5319e7', f'{name} ownership area') for name in ('dashboard', 'evaluation', 'repository')},
    **{f'priority:{name}': (color, 'Roadmap priority') for name, color in
       (('must', 'b60205'), ('should', 'fbca04'), ('nice', '0e8a16'))},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-labels', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pr-head')
    parser.add_argument('--pr-title')
    parser.add_argument('--pr-body-file', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output path')
    root = Path(__file__).resolve().parents[1]
    credential = subprocess.run(
        ['git', '-c', f'safe.directory={root.as_posix()}', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', capture_output=True,
        text=True, timeout=30, cwd=root)
    values = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
    token = values.get('password')
    if not token:
        raise SystemExit('Git credential helper supplied no credential; no GitHub mutation attempted')
    def api(path, data=None, method=None):
        request = Request('https://api.github.com/' + path,
                          data=json.dumps(data).encode() if data is not None else None,
                          method=method,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json',
                                   'Content-Type': 'application/json', 'User-Agent': 'HoloGuide-Person4'})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            body = json.loads(exc.read())
            return {'http_error': exc.code, 'message': body.get('message', 'request failed')}
    user = api('user')
    if user.get('login') != 'OmarWaled234':
        raise SystemExit(f"Credential account is {user.get('login', 'unknown')}; expected confirmed OmarWaled234. No mutation attempted.")
    prefix = 'repos/' + REPOSITORY
    repo = api(prefix)
    existing = api(prefix + '/labels?per_page=100')
    report = {'account': user['login'], 'repository': REPOSITORY,
              'permissions': repo.get('permissions'), 'existing_labels': existing,
              'rulesets': api(prefix + '/rulesets?per_page=100'),
              'workflows': api(prefix + '/actions/workflows'), 'protection': {}, 'labels': []}
    for branch in ('main', 'dev'):
        state = api(prefix + '/branches/' + branch)
        report['protection'][branch] = state if 'http_error' in state else {
            'protected': state.get('protected'),
            'details': api(prefix + '/branches/' + branch + '/protection')}
    if isinstance(existing, list):
        known = {label['name'].casefold() for label in existing}
        for name, (color, description) in LABELS.items():
            if name.casefold() in known:
                report['labels'].append({'name': name, 'status': 'already exists'})
            elif args.apply_labels:
                response = api(prefix + '/labels', {'name': name, 'color': color, 'description': description})
                report['labels'].append({'name': name, 'result': response})
            else:
                report['labels'].append({'name': name, 'status': 'missing; read-only run'})
    report['branch_protection_mutated'] = False
    report['admin_action'] = ('Existing protection retained. Owner must enforce one approval, '
        'block force-push/deletion, preserve stronger rules, and require checks only after reliable CI exists.')
    if args.pr_head:
        if not args.pr_title or not args.pr_body_file:
            parser.error('--pr-head requires --pr-title and --pr-body-file')
        existing_prs = api(prefix + '/pulls?' + urlencode({'state': 'open', 'head': 'OmarElkott:' + args.pr_head}))
        if not isinstance(existing_prs, list):
            report['pull_request'] = existing_prs
        else:
            body = args.pr_body_file.read_text(encoding='utf-8')
            if existing_prs:
                pr = api(prefix + f"/pulls/{existing_prs[0]['number']}",
                         {'title': args.pr_title, 'body': body}, method='PATCH')
            else:
                pr = api(prefix + '/pulls', {'head': args.pr_head, 'base': 'main', 'draft': True,
                                            'title': args.pr_title, 'body': body})
            report['pull_request'] = (api(prefix + f"/pulls/{pr['number']}") if 'number' in pr else pr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'account': report['account'], 'permissions': report['permissions'],
                      'protection': report['protection'],
                      'pull_request': {key: report.get('pull_request', {}).get(key) for key in
                                       ('number', 'html_url', 'state', 'draft', 'http_error', 'message')},
                      'labels': [
                          {'name': item['name'], 'status': item.get('status') or
                           ('created' if item.get('result', {}).get('name') else item.get('result'))}
                          for item in report['labels']]}))


if __name__ == '__main__':
    main()
