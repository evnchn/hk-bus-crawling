"""Self-check for link_rail_interchanges().

Run: python crawling/test_mergeStopList.py
"""
from mergeStopList import RAIL_INTERCHANGE_DISTANCE_THRESHOLD
from mergeStopList import link_rail_interchanges


def stop(name, lat, lng):
  return {
      'name': {'zh': name, 'en': name},
      'location': {'lat': lat, 'lng': lng},
  }


# Tin Shui Wai: real coordinates, 232m apart, too far for the proximity search
STOP_LIST = {
    'TIS': stop('天水圍', 22.44788, 114.00447),
    'LR430': stop('天水圍', 22.44945, 114.00596),
    'LR999': stop('天耀', 22.45800, 114.00300),
}
ROUTE_LIST = {
    'TML': {'stops': {'mtr': ['TIS']}},
    '705': {'stops': {'lightRail': ['LR430', 'LR999']}},
}


def test_links_same_name_platforms_both_ways():
  stop_map = {}
  assert link_rail_interchanges(ROUTE_LIST, STOP_LIST, stop_map) == 2
  assert stop_map['TIS'] == [['lightRail', 'LR430']]
  assert stop_map['LR430'] == [['mtr', 'TIS']]
  assert 'LR999' not in stop_map  # different name, left alone


def test_is_idempotent_and_keeps_existing_entries():
  stop_map = {'TIS': [['kmb', 'SOME_BUS_STOP']]}
  link_rail_interchanges(ROUTE_LIST, STOP_LIST, stop_map)
  assert link_rail_interchanges(ROUTE_LIST, STOP_LIST, stop_map) == 0
  assert stop_map['TIS'] == [['kmb', 'SOME_BUS_STOP'], ['lightRail', 'LR430']]


def test_ignores_same_name_stops_beyond_the_distance_threshold():
  far = dict(STOP_LIST, LR430=stop('天水圍', 22.44788 + 0.02, 114.00447))
  assert RAIL_INTERCHANGE_DISTANCE_THRESHOLD < 2000
  stop_map = {}
  assert link_rail_interchanges(ROUTE_LIST, far, stop_map) == 0
  assert stop_map == {}


def test_skips_routed_but_un_geocoded_stops():
  # a stop can be routed but missing from stop_list, or present without a
  # name, see the geocoding failure path in lightRail.py
  routes = {
      '705': {'stops': {'lightRail': ['LR430', 'DANGLING', 'NAMELESS_LR']}},
      'TML': {'stops': {'mtr': ['TIS', 'NAMELESS_MTR']}},
      'NO_STOPS_KEY': {},
  }
  nameless = {'location': {'lat': 22.4, 'lng': 114.0}}
  stops = dict(STOP_LIST, NAMELESS_LR=nameless, NAMELESS_MTR=nameless)
  stop_map = {}
  # the two nameless stops must not pair up on their shared empty name
  assert link_rail_interchanges(routes, stops, stop_map) == 2
  assert sorted(stop_map) == ['LR430', 'TIS']


if __name__ == '__main__':
  test_links_same_name_platforms_both_ways()
  test_is_idempotent_and_keeps_existing_entries()
  test_ignores_same_name_stops_beyond_the_distance_threshold()
  test_skips_routed_but_un_geocoded_stops()
  print('ok')
