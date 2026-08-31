"""Self-check for the rail station area grouping.

Run: python crawling/test_mergeStopList.py
"""
import json
import logging
import os
import tempfile

from mergeStopList import is_point_in_ring
from mergeStopList import link_rail_station_areas
from mergeStopList import valid_ring

# a square roughly 300m across, and a second one far away
SQUARE = [[114.000, 22.440], [114.003, 22.440],
          [114.003, 22.443], [114.000, 22.443], [114.000, 22.440]]
ELSEWHERE = [[114.100, 22.540], [114.103, 22.540],
             [114.103, 22.543], [114.100, 22.543], [114.100, 22.540]]


def areas_file(*rings):
  fd, path = tempfile.mkstemp(suffix='.geojson')
  with os.fdopen(fd, 'w', encoding='UTF-8') as f:
    features = [{'geometry': {'type': 'Polygon', 'coordinates': [r]}}
                for r in rings]
    json.dump({'features': features}, f)
  return path


def stop(lat, lng, name='x'):
  return {'name': {'zh': name, 'en': name},
          'location': {'lat': lat, 'lng': lng}}


STOP_LIST = {
    'HEAVY': stop(22.4405, 114.0005),   # inside SQUARE
    'LIGHT': stop(22.4425, 114.0025),   # inside SQUARE, ~300m from HEAVY
    'FAR': stop(22.4600, 114.0600),     # inside no area
}
ROUTE_LIST = {
    'TML': {'stops': {'mtr': ['HEAVY']}},
    '751': {'stops': {'lightRail': ['LIGHT', 'FAR']}},
}


def test_point_in_ring():
  assert is_point_in_ring(22.4415, 114.0015, SQUARE)
  assert not is_point_in_ring(22.4415, 114.0035, SQUARE)   # east of it
  assert not is_point_in_ring(22.4455, 114.0015, SQUARE)   # north of it


def test_pairs_the_two_operators_inside_one_area():
  path = areas_file(SQUARE, ELSEWHERE)
  stop_map = {}
  assert link_rail_station_areas(ROUTE_LIST, STOP_LIST, stop_map, path) == 2
  assert stop_map['HEAVY'] == [['lightRail', 'LIGHT']]
  assert stop_map['LIGHT'] == [['mtr', 'HEAVY']]
  assert 'FAR' not in stop_map          # outside every area
  os.unlink(path)


def test_never_pairs_within_one_operator_and_makes_no_empty_groups():
  routes = {'751': {'stops': {'lightRail': ['HEAVY', 'LIGHT']}}}
  path = areas_file(SQUARE)
  stop_map = {}
  # both are lightRail now, so the area must not join them -- direction is
  # get_stop_group()'s call, not ours -- and must not leave empty groups behind
  assert link_rail_station_areas(routes, STOP_LIST, stop_map, path) == 0
  assert stop_map == {}
  os.unlink(path)


def test_is_idempotent_and_keeps_existing_entries():
  path = areas_file(SQUARE)
  stop_map = {'HEAVY': [['kmb', 'SOME_BUS_STOP']]}
  link_rail_station_areas(ROUTE_LIST, STOP_LIST, stop_map, path)
  assert link_rail_station_areas(ROUTE_LIST, STOP_LIST, stop_map, path) == 0
  assert stop_map['HEAVY'] == [['kmb', 'SOME_BUS_STOP'],
                               ['lightRail', 'LIGHT']]
  os.unlink(path)


def test_survives_a_missing_areas_file_and_un_geocoded_stops():
  missing = '/no/such.geojson'
  assert link_rail_station_areas(ROUTE_LIST, STOP_LIST, {}, missing) == 0
  routes = {'751': {'stops': {'lightRail': ['LIGHT', 'NOT_IN_STOP_LIST']}},
            'TML': {'stops': {'mtr': ['HEAVY']}},
            'NO_STOPS_KEY': {}}
  stops = dict(STOP_LIST, NO_LOCATION={'name': {'zh': 'x', 'en': 'x'}})
  routes['751']['stops']['lightRail'].append('NO_LOCATION')
  path = areas_file(SQUARE)
  stop_map = {}
  assert link_rail_station_areas(routes, stops, stop_map, path) == 2
  assert sorted(stop_map) == ['HEAVY', 'LIGHT']
  os.unlink(path)


def test_a_bad_hand_edit_is_skipped_not_fatal():
  good = {'geometry': {'type': 'Polygon', 'coordinates': [SQUARE]}}
  assert valid_ring(good) == SQUARE
  for bad in [{},
              {'geometry': {}},
              {'geometry': {'type': 'Point', 'coordinates': [1, 2]}},
              {'geometry': {'type': 'Polygon', 'coordinates': []}},
              # drawn as a line rather than an area in a map editor
              {'geometry': {'type': 'MultiLineString',
                            'coordinates': [SQUARE]}},
              {'geometry': {'type': 'Polygon', 'coordinates': [SQUARE[:3]]}},
              {'geometry': {'type': 'Polygon', 'coordinates': [SQUARE[:-1]]}},
              {'geometry': {'type': 'Polygon', 'coordinates': [[['a', 'b']] * 4]}}]:
    assert valid_ring(bad) is None, bad
  # a whole file of junk still returns cleanly
  fd, path = tempfile.mkstemp(suffix='.geojson')
  with os.fdopen(fd, 'w', encoding='UTF-8') as f:
    json.dump({'features': [{}, {'geometry': {'type': 'Point'}}]}, f)
  assert link_rail_station_areas(ROUTE_LIST, STOP_LIST, {}, path) == 0
  os.unlink(path)


def test_warns_when_one_area_covers_two_stations():
  """A polygon drawn too wide swallows a neighbour, so say so."""
  path = areas_file(SQUARE)
  stop_list = dict(STOP_LIST, LIGHT=stop(22.4425, 114.0025, 'somewhere else'))
  records = []
  handler = logging.Handler()
  handler.emit = records.append
  logger = logging.getLogger('mergeStopList')
  logger.addHandler(handler)
  try:
    link_rail_station_areas(ROUTE_LIST, stop_list, {}, areas_path=path)
  finally:
    logger.removeHandler(handler)
    os.remove(path)
  warnings = [r for r in records if r.levelno == logging.WARNING]
  assert warnings, 'a mixed area must warn'
  assert 'somewhere else' in warnings[0].getMessage()


def test_a_nameless_stop_is_not_a_second_station():
  """A stop with no zh name is missing data, not a neighbouring station."""
  path = areas_file(SQUARE)
  nameless = {'name': {'en': 'x'}, 'location': {'lat': 22.4425, 'lng': 114.0025}}
  records = []
  handler = logging.Handler()
  handler.emit = records.append
  logger = logging.getLogger('mergeStopList')
  logger.addHandler(handler)
  try:
    link_rail_station_areas(
        ROUTE_LIST, dict(STOP_LIST, LIGHT=nameless), {}, areas_path=path)
  finally:
    logger.removeHandler(handler)
    os.remove(path)
  assert not [r for r in records if r.levelno == logging.WARNING]


def test_shipped_areas_are_usable():
  here = os.path.dirname(os.path.abspath(__file__))
  path = os.path.join(here, 'railStationAreas.geojson')
  with open(path, encoding='UTF-8') as f:
    collection = json.load(f)
  assert collection['type'] == 'FeatureCollection'
  features = collection['features']
  names = [x['properties']['name_zh'] for x in features]
  assert len(names) == len(set(names)), 'duplicate station area'
  for feature in features:
    name = feature['properties']['name_zh']
    assert len(feature['geometry']['coordinates']) == 1, f'{name} has a hole'
    assert valid_ring(feature) is not None, f'{name} is not a usable polygon'


if __name__ == '__main__':
  test_point_in_ring()
  test_pairs_the_two_operators_inside_one_area()
  test_never_pairs_within_one_operator_and_makes_no_empty_groups()
  test_is_idempotent_and_keeps_existing_entries()
  test_survives_a_missing_areas_file_and_un_geocoded_stops()
  test_a_bad_hand_edit_is_skipped_not_fatal()
  test_warns_when_one_area_covers_two_stations()
  test_a_nameless_stop_is_not_a_second_station()
  test_shipped_areas_are_usable()
  print('ok')
