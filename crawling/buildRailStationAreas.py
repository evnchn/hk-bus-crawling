"""Generate a first-draft railStationAreas.geojson from data already crawled.

The rail station areas are checked in as editable data, NOT regenerated on every
run, so hand-drawn corrections survive. Re-run this only when a station opens or
moves, and review the diff:

    pip install shapely            # dev-only, not a crawler dependency
    python crawling/buildRailStationAreas.py

Inputs are both produced by this pipeline: exits.mtr.json (mtrExits.py) and the
stop coordinates in routeFareList.mergeRoutes.min.json (mergeRoutes.py).

An area is the convex hull of a station's MTR exits plus its own rail stops,
buffered by BUFFER_METRES. The buffer has a wide safe band: anything from 30m to
70m yields the same four MTR/Light Rail interchanges and captures no foreign
station's rail stop. It breaks at 90m, where Tsim Sha Tsui East swallows Tsim
Sha Tsui.
"""
import json
import math
import re
import sys

RAIL_CO = ('mtr', 'lightRail')
BUFFER_METRES = 50
SIMPLIFY_METRES = 3

# local equirectangular projection, good enough over one city
LAT_ORIGIN = 22.4
METRES_PER_DEGREE_LAT = 111320.0
METRES_PER_DEGREE_LNG = METRES_PER_DEGREE_LAT * math.cos(
    math.radians(LAT_ORIGIN))


def to_metres(lat, lng):
  return (lng * METRES_PER_DEGREE_LNG, lat * METRES_PER_DEGREE_LAT)


def to_degrees(x, y):
  return (round(x / METRES_PER_DEGREE_LNG, 6),
          round(y / METRES_PER_DEGREE_LAT, 6))


def build(db, exits):
  from shapely.geometry import MultiPoint

  stop_list = db['stopList']
  rail_stops = {}
  for route in db['routeList'].values():
    for co, co_stops in route.get('stops', {}).items():
      if co in RAIL_CO:
        for stop_id in co_stops:
          rail_stops.setdefault(stop_id, stop_list[stop_id])

  exits_by_name = {}
  for exit_ in exits:
    exits_by_name.setdefault(exit_['name']['zh'], []).append(
        to_metres(exit_['lat'], exit_['lng']))

  names = {}
  for stop in rail_stops.values():
    station = names.setdefault(stop['name']['zh'],
                               {'en': stop['name']['en'], 'points': []})
    station['points'].append(
        to_metres(stop['location']['lat'], stop['location']['lng']))

  features = []
  for name_zh in sorted(names):
    station = names[name_zh]
    station_exits = exits_by_name.get(name_zh, [])
    has_exits = len(station_exits) >= 3
    source = 'mtr exits + rail stops' if has_exits else 'rail stops'
    area = MultiPoint(station_exits + station['points']).convex_hull \
        .buffer(BUFFER_METRES, quad_segs=2).simplify(SIMPLIFY_METRES)
    features.append({
        'type': 'Feature',
        'properties': {
            'name_zh': name_zh,
            'name_en': station['en'],
            'source': f'generated: {source}, buffered {BUFFER_METRES}m',
        },
        'geometry': {
            'type': 'Polygon',
            'coordinates': [[to_degrees(x, y) for x, y in area.exterior.coords]],
        },
    })
  return {'type': 'FeatureCollection', 'features': features}


def main():
  with open('routeFareList.mergeRoutes.min.json', 'r', encoding='UTF-8') as f:
    db = json.load(f)
  with open('exits.mtr.json', 'r', encoding='UTF-8') as f:
    exits = json.load(f)
  collection = build(db, exits)
  text = json.dumps(collection, ensure_ascii=False, indent=1)
  # one line per coordinate pair, so a hand edit shows up as a one-line diff
  text = re.sub(r'\[\n\s+(-?[\d.]+),\n\s+(-?[\d.]+)\n\s+\]', r'[\1, \2]', text)
  with open('crawling/railStationAreas.geojson', 'w', encoding='UTF-8') as f:
    f.write(text + '\n')
  print(f'wrote {len(collection["features"])} station areas')


if __name__ == '__main__':
  try:
    import shapely  # noqa: F401
  except ImportError:
    sys.exit('this generator needs shapely: pip install shapely')
  main()
