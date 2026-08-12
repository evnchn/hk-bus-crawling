import collections
import json
import sys

BASELINE = 'crawling/unmatched.baseline.json'
SOURCE = 'routeFareList.min.json'


def collect():
  with open(SOURCE, 'r', encoding='UTF-8') as f:
    routeList = json.load(f)['routeList']
  unmatched = {}
  total = collections.Counter()
  for key, route in routeList.items():
    for co in route['bound']:
      total[co] += 1
    if route['gtfsId'] is None:
      unmatched[key] = sorted(route['bound'])
  return unmatched, total


def counts(unmatched):
  c = collections.Counter()
  for cos in unmatched.values():
    for co in cos:
      c[co] += 1
  return c


def main():
  unmatched, total = collect()
  now = counts(unmatched)

  if '--update' in sys.argv:
    with open(BASELINE, 'w', encoding='UTF-8') as f:
      json.dump({'total': sum(total.values()),
                 'unmatched': sum(1 for _ in unmatched),
                 'perOperator': dict(sorted(now.items())),
                 'routeDirs': dict(sorted(unmatched.items()))},
                f, ensure_ascii=False, indent=1)
    print('wrote %s: %d unmatched of %d route-dirs'
          % (BASELINE, len(unmatched), sum(total.values())))
    return 0

  with open(BASELINE, 'r', encoding='UTF-8') as f:
    base = json.load(f)
  was = collections.Counter(base['perOperator'])

  print('unmatched route-dirs: %d (baseline %d) of %d'
        % (len(unmatched), base['unmatched'], sum(total.values())))
  print('%-14s %8s %8s %8s' % ('operator', 'baseline', 'now', 'delta'))
  for co in sorted(set(was) | set(now)):
    print('%-14s %8d %8d %+8d' % (co, was[co], now[co], now[co] - was[co]))

  appeared = sorted(set(unmatched) - set(base['routeDirs']))
  resolved = sorted(set(base['routeDirs']) - set(unmatched))
  for label, keys in (('newly unmatched', appeared),
                      ('now matched', resolved)):
    print('\n%s (%d):' % (label, len(keys)))
    for key in keys:
      print('   %s' % key)

  known = {(key.split('+')[0], co)
           for key, cos in base['routeDirs'].items() for co in cos}
  fresh = sorted(key for key in appeared
                 if any((key.split('+')[0], co) not in known
                        for co in unmatched[key]))
  regressed = sorted(co for co in now if now[co] > was[co])
  if regressed or fresh:
    if regressed:
      print(
          '\nFAIL: %s got worse' %
          ', '.join(
              '%s %+d' %
              (co, now[co] - was[co]) for co in regressed))
    if fresh:
      print('\nFAIL: route numbers that were matched before: %s'
            % ', '.join(sorted({key.split('+')[0] for key in fresh})))
    print('rerun with --update only if intended')
    return 1
  print('\nOK')
  return 0


if __name__ == '__main__':
  sys.exit(main())
