"""Checks for crawling/summarizeChanges.py, on snapshots built here in full.

Run directly (``python crawling/test_summarizeChanges.py``) or under pytest.
Every assertion has been seen to fail with the matching logic broken; a green
run that cannot go red proves nothing.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from summarizeChanges import (  # noqa: E402
    MAX_TELEGRAM_CHARS,
    diff,
    has_changes,
    render_html,
    render_telegram,
    telegram_length,
    write_summary,
)


def test_summarize_changes():
  """Exercise every branch of the comparison on hand-built snapshots."""
  def stop(lat, lng, zh, en='EN'):
    return {'location': {'lat': lat, 'lng': lng}, 'name': {'zh': zh, 'en': en}}

  before = {
      'holidays': ['2026-01-01'],
      'serviceDayMap': {},
      'stopMap': {},
      'stopList': {
          'A': stop(22.30, 114.17, '甲站'),
          'B': stop(22.31, 114.18, '乙站'),
          'C': stop(22.32, 114.19, '丙站'),
          'GONE': stop(22.33, 114.20, '將刪站'),
      },
      'routeList': {
          '1+1+OLD TOWN+PIER': {
              'route': '1', 'co': ['kmb'], 'serviceType': 1, 'bound': {'kmb': 'O'},
              'orig': {'en': 'OLD TOWN', 'zh': '舊城'},
              'dest': {'en': 'PIER', 'zh': '碼頭'},
              'stops': {'kmb': ['A', 'B']}, 'fares': ['5.0'], 'faresHoliday': None,
              'freq': {'1': {}}, 'jt': '20', 'gtfsId': '1', 'nlbId': None, 'seq': 1,
          },
          '9+1+NOWHERE+VOID': {
              'route': '9', 'co': ['ctb'], 'serviceType': 1, 'bound': {'ctb': 'O'},
              'orig': {'en': 'NOWHERE', 'zh': '無'}, 'dest': {'en': 'VOID', 'zh': '空'},
              'stops': {'ctb': ['C']}, 'fares': ['3.0'], 'faresHoliday': None,
              'freq': {}, 'jt': '5', 'gtfsId': '9', 'nlbId': None, 'seq': 2,
          },
      },
  }

  after = json.loads(json.dumps(before))
  after['holidays'] = ['2026-01-01', '2026-02-17']
  # a stop is renamed, another is nudged 100 m up the road, one is dropped
  after['stopList']['B']['name']['zh'] = '乙站 (新名)'
  after['stopList']['C']['location']['lat'] = 22.3209
  del after['stopList']['GONE']
  after['stopList']['NEW'] = stop(22.34, 114.21, '新站')
  # route 1 keeps its identity but gains a stop, a fare rise and a new terminus
  route = after['routeList'].pop('1+1+OLD TOWN+PIER')
  route['dest'] = {'en': 'FERRY PIER', 'zh': '渡輪碼頭'}
  route['stops']['kmb'] = ['A', 'B', 'NEW']
  route['fares'] = ['6.0']
  after['routeList']['1+1+OLD TOWN+FERRY PIER'] = route
  # route 9 is withdrawn, route 2 is brand new and points at a missing stop
  del after['routeList']['9+1+NOWHERE+VOID']
  after['routeList']['2+1+NORTH+SOUTH'] = {
      'route': '2',
      'co': ['gmb'],
      'serviceType': 1,
      'bound': {
          'gmb': 'O'},
      'orig': {
          'en': 'NORTH',
          'zh': '北'},
      'dest': {
          'en': 'SOUTH',
          'zh': '南'},
      'stops': {
          'gmb': [
              'A',
              'MISSING']},
      'fares': ['4.0'],
      'faresHoliday': None,
      'freq': {},
      'jt': '9',
            'gtfsId': None,
            'nlbId': None,
            'seq': 3,
  }

  result = diff(before, after)
  routes, stops, health = result['routes'], result['stops'], result['health']

  # the terminus change must read as one route that moved, not a delete plus
  # an add
  assert [r['route'] for r in routes['added']] == ['2'], routes['added']
  assert [r['route'] for r in routes['removed']] == ['9'], routes['removed']
  changed = {r['route']: r for r in routes['changed']}
  assert set(changed) == {'1'}, changed
  kinds = {c['type']: c for c in changed['1']['changes']}
  assert kinds['terminus']['after'] == '渡輪碼頭', kinds['terminus']
  assert changed['1']['previous']['key'] == '1+1+OLD TOWN+PIER'
  assert [s['id'] for s in kinds['stop_added']['stops']] == ['NEW']
  fares = [c for c in changed['1']['changes'] if c.get('field') == 'fares']
  assert fares and fares[0]['after'] == '6.0', fares

  assert [s['id'] for s in stops['added']] == ['NEW']
  assert [s['id'] for s in stops['removed']] == ['GONE']
  assert [s['after'] for s in stops['renamed']] == ['乙站 (新名)']
  assert [s['id'] for s in stops['moved']] == ['C'], stops['moved']
  assert 90 < stops['moved'][0]['metres'] < 110, stops['moved'][0]

  assert health['dangling_stop_refs'] == 1, health
  # withdrawing route 9 leaves stop C served by nothing
  assert health['orphan_stops'] == ['C'], health['orphan_stops']
  assert result['health_before']['orphan_stops'] == [
      'GONE'], result['health_before']
  assert result['holidays']['added'] == ['2026-02-17']

  # an unchanged pair must produce a completely quiet report
  quiet = diff(after, after)
  assert not any(quiet['routes'][k]
                 for k in ('added', 'removed', 'changed')), quiet['routes']
  assert not any(quiet['stops'][k]
                 for k in ('added', 'removed', 'renamed', 'moved'))

  # both renderers must survive the loud case and the quiet one
  for payload in (result, quiet):
    page = render_html(payload, {'generated': '2026-01-01 00:00',
                                 'before_label': 'a', 'after_label': 'b'})
    assert page.lstrip().startswith('<!DOCTYPE html>') and '</html>' in page
    message = render_telegram(
        payload, {
            'after_label': 'b', 'page_url': 'https://example.com'})
    assert telegram_length(
        message) <= MAX_TELEGRAM_CHARS, telegram_length(message)
  assert '今次更新無資料變更' in render_telegram(quiet, {'after_label': 'b'})
  assert has_changes(result) and not has_changes(quiet)

  # An emoji is one Python character but two of Telegram's, and truncation must
  # cut on a line boundary so no HTML tag is ever left half-written
  # A reroute keeps its identity however much of the middle changed. CTB
  # rerouted E11B's Tung Chung tail on 2026-08-17 and only 19 of 29 stops
  # survived; scoring the stop overlap would have called that two unrelated
  # routes, so identity must come from route_group alone.
  reroute_before = json.loads(json.dumps(before))
  reroute_after = json.loads(json.dumps(before))
  for name in 'PQRSTUVW':
    reroute_before['stopList'][name] = stop(22.3, 114.17, name)
    reroute_after['stopList'][name] = stop(22.3, 114.17, name)

  def e11b(dest_en, dest_zh, stops):
    return {
        '11B+1+STATION+' +
        dest_en: {
            'route': '11B',
            'co': ['ctb'],
            'serviceType': 1,
            'bound': {
                'ctb': 'OI'},
            'orig': {
                'en': 'STATION',
                'zh': '站'},
            'dest': {
                'en': dest_en,
                'zh': dest_zh},
            'stops': {
                'ctb': stops},
            'fares': ['1.0'],
            'faresHoliday': None,
            'freq': {},
            'jt': '1',
            'gtfsId': None,
            'nlbId': None,
            'seq': 1}}
  reroute_before['routeList'] = e11b('OLD MALL', '舊商場', ['P', 'Q', 'R', 'S'])
  reroute_after['routeList'] = e11b('NEW ESTATE', '新邨', ['P', 'T', 'U', 'V'])
  reroute = diff(reroute_before, reroute_after)
  assert not reroute['routes']['added'], reroute['routes']['added']
  assert not reroute['routes']['removed'], reroute['routes']['removed']
  assert reroute['routes']['changed'][0]['previous']['dest']['zh'] == '舊商場'

  # Two keys leaving one group and two joining it cannot be paired without
  # guessing, so they must stay reported as separate removals and additions.
  ambiguous_before = json.loads(json.dumps(reroute_before))
  ambiguous_after = json.loads(json.dumps(reroute_before))
  ambiguous_before['routeList'] = {
      **e11b('MALL A', '商場甲', ['P', 'Q']), **e11b('MALL B', '商場乙', ['R', 'S'])}
  ambiguous_after['routeList'] = {
      **e11b('MALL C', '商場丙', ['P', 'Q']), **e11b('MALL D', '商場丁', ['R', 'S'])}
  ambiguous = diff(ambiguous_before, ambiguous_after)
  assert len(ambiguous['routes']['added']) == 2, ambiguous['routes']['added']
  assert len(ambiguous['routes']['removed']
             ) == 2, ambiguous['routes']['removed']
  assert not ambiguous['routes']['changed'], ambiguous['routes']['changed']

  assert telegram_length('🚌') == 2 and len('🚌') == 1
  flood = json.loads(json.dumps(after))
  for i in range(400):
    flood['routeList'][f'{i}+1+A+B'] = {
        'route': str(i),
        'co': ['kmb'],
        'serviceType': 1,
        'bound': {
            'kmb': 'O'},
        'orig': {
            'en': 'A',
            'zh': '甲'},
        'dest': {
            'en': 'B',
            'zh': '乙'},
        'stops': {
            'kmb': ['A']},
        'fares': ['1.0'],
        'faresHoliday': None,
        'freq': {},
        'jt': '1',
        'gtfsId': None,
        'nlbId': None,
        'seq': i,
    }
  long_message = render_telegram(
      diff(
          before, flood), {
          'after_label': 'b', 'page_url': 'https://example.com'})
  assert telegram_length(
      long_message) <= MAX_TELEGRAM_CHARS, telegram_length(long_message)
  assert long_message.endswith(
      '太長，請看 🔗 https://example.com'), long_message[-60:]
  assert long_message.count('<b>') == long_message.count(
      '</b>'), 'cut a tag in half'

  # Telegram's HTML parse mode breaks on a raw ampersand or angle bracket, and
  # real stop names contain both
  hostile = json.loads(json.dumps(after))
  hostile['stopList']['NEW']['name']['zh'] = 'Zoological & Botanical <Garden>'
  message = render_telegram(diff(before, hostile), {'after_label': 'a & b'})
  assert '&amp;' in message and '&lt;Garden&gt;' in message, message
  assert 'Botanical <' not in message and '<code>a & b' not in message, message

  # write_summary must skip the Telegram file on a quiet run and produce one
  # on a loud run
  with tempfile.TemporaryDirectory() as tmp:
    for payload, expected in ((after, True), (before, False)):
      message = os.path.join(tmp, 'message.txt')
      page = os.path.join(tmp, 'page.html')
      write_summary(before, payload, html_path=page, telegram_path=message)
      assert os.path.exists(page)
      assert os.path.exists(message) is expected, (payload is after, expected)
      os.remove(page)
      if os.path.exists(message):
        os.remove(message)


if __name__ == '__main__':
  test_summarize_changes()
  print('checks passed')
