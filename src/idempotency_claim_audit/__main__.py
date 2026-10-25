"""Command line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(prog='idempotency-claim-audit', description='Offline audit of documented re-run claims against Python source.')
    parser.add_argument('path', nargs='?', default='.', help='project directory (default: current directory)')
    parser.add_argument('--json', action='store_true', help='emit a JSON report')
    parser.add_argument('--verbose', action='store_true', help='include claim locations and verified families')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB', help='ignore relative paths matching a glob; repeatable')
    parser.add_argument('--fail-on', choices=('MEDIUM', 'HIGH'), default='MEDIUM', help='minimum failing severity (default: MEDIUM)')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.ignore)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    result['status'] = 'ERROR' if result['errors'] else ('FAIL' if failed else 'PASS')
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['status'])
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['rule'])
            if finding['note']:
                print('  ' + finding['note'])
            for evidence in finding['evidence']:
                print('  ' + evidence.get('path', '') + ':' + str(evidence.get('line', '')) + ' ' + evidence['kind'])
        if args.verbose:
            for claim in result['claims']:
                print('claim ' + claim['family'] + ' ' + claim['path'] + ':' + str(claim['line']))
            for claim in result['verified_claims']:
                print('verified ' + claim['family'])
        for error in result['errors']:
            print('error ' + error['path'] + ': ' + error['message'], file=sys.stderr)
    return 1 if result['errors'] else (2 if failed else 0)


if __name__ == '__main__':
    sys.exit(main())
