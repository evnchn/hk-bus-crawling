"""Summarize what changed between two crawls of ``routeFareList.min.json``.

Answers two questions from one pair of snapshots:

* what a rider would notice - new and withdrawn routes, moved termini, added
  and dropped stops, fare and timetable updates;
* what a maintainer needs to chase - stop ids a route points at that no longer
  exist, stops no route serves, coordinates outside Hong Kong, routes with no
  stops at all.

Writes a static page for GitHub Pages and a Telegram-ready message.
``routeCompare.py`` calls :func:`write_summary` with the two snapshots it has
already downloaded; the command line is here for reruns against saved files:

    python3 crawling/summarizeChanges.py previous.min.json routeFareList.min.json \
        --json summary.json --html summary.html --telegram message.txt

Checks live in ``crawling/test_summarizeChanges.py``.
"""
import argparse
import html
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

MOVED_STOP_METRES = 20.0


def metres(a, b):
  """Equirectangular approximation - plenty for a 'did this stop move' check."""
  lat1, lng1 = a['lat'], a['lng']
  lat2, lng2 = b['lat'], b['lng']
  mean = math.radians((lat1 + lat2) / 2)
  x = math.radians(lng2 - lng1) * math.cos(mean)
  y = math.radians(lat2 - lat1)
  return math.hypot(x, y) * 6371008.8


def stop_ids(route):
  """Flatten a route's per-company stop lists into an ordered id list."""
  out = []
  for co in sorted(route.get('stops') or {}):
    out.extend(route['stops'][co])
  return out


def route_group(route):
  """Coarse identity: stable across terminus renames, not unique on its own."""
  bound = route.get('bound') or {}
  return (
      route.get('route'),
      ','.join(sorted(route.get('co') or [])),
      str(route.get('serviceType')),
      ','.join(f'{k}:{bound[k]}' for k in sorted(bound)),
  )


def pair_renames(removed_keys, added_keys, old_routes, new_routes):
  """Re-pair a vanished route key with an appeared one when they are the same
  route under a new terminus.

  A routeList key is route+serviceType+ORIG+DEST, so changing where a route
  ends renames its key, and a plain set difference reports that as one deletion
  plus one unrelated addition. route_group() strips the termini out of the
  identity; if exactly one key left a group and exactly one joined it, they are
  the same route and the difference is the terminus.

  When two or more keys move on either side of a group there is no way to tell
  which became which, so those stay reported as separate removals and
  additions rather than guessed at.
  """
  groups = defaultdict(lambda: ([], []))
  for key in removed_keys:
    groups[route_group(old_routes[key])][0].append(key)
  for key in added_keys:
    groups[route_group(new_routes[key])][1].append(key)

  pairs = [(gone[0], arrived[0]) for gone, arrived in groups.values()
           if len(gone) == 1 == len(arrived)]
  return pairs, {new_key for _, new_key in pairs}


def name_of(stop_list, stop_id):
  stop = stop_list.get(stop_id)
  if not stop:
    return stop_id
  return (stop.get('name') or {}).get('zh') or (
      stop.get('name') or {}).get('en') or stop_id


def compare_route(old, new, old_stops, new_stops):
  """Field-level changes for one route that exists in both snapshots."""
  changes = []

  for field, label in (('orig', 'orig'), ('dest', 'dest')):
    before, after = (old.get(field) or {}), (new.get(field) or {})
    if before.get('en') != after.get(
            'en') or before.get('zh') != after.get('zh'):
      changes.append({
          'type': 'terminus',
          'field': label,
          'before': before.get('zh') or before.get('en'),
          'after': after.get('zh') or after.get('en'),
      })

  before_ids, after_ids = stop_ids(old), stop_ids(new)
  added = [s for s in after_ids if s not in set(before_ids)]
  removed = [s for s in before_ids if s not in set(after_ids)]
  if added:
    changes.append({'type': 'stop_added', 'stops': [
        {'id': s, 'name': name_of(new_stops, s)} for s in added]})
  if removed:
    changes.append({'type': 'stop_removed', 'stops': [
        {'id': s, 'name': name_of(old_stops, s)} for s in removed]})
  if not added and not removed and before_ids != after_ids:
    changes.append({'type': 'stop_reordered', 'count': len(after_ids)})

  # 'seq' is a presentation index and 'stops' is covered above; both churn
  # whenever anything else does, so they are not reported as changes.
  for field in ('fares', 'faresHoliday'):
    before, after = summarize_fares(
        old.get(field)), summarize_fares(
        new.get(field))
    if before != after:
      changes.append({'type': 'field', 'field': field,
                      'before': before, 'after': after})
  for field in ('freq', 'jt', 'gtfsId', 'nlbId'):
    if old.get(field) != new.get(field):
      entry = {'type': 'field', 'field': field}
      if field != 'freq':
        entry['before'], entry['after'] = old.get(field), new.get(field)
      changes.append(entry)

  return changes


def summarize_fares(fares):
  if not fares:
    return None
  try:
    values = sorted({float(f) for f in fares})
  except (TypeError, ValueError):
    return None
  return f'{values[0]:.1f}' if len(
      values) == 1 else f'{values[0]:.1f}-{values[-1]:.1f}'


def health(data):
  """Data-quality flags a maintainer would want raised (issue #76: 'identify
  the data-related problems like stops or routes missing easily')."""
  routes, stops = data['routeList'], data['stopList']
  dangling = defaultdict(list)
  referenced = set()
  for key, route in routes.items():
    for stop_id in stop_ids(route):
      referenced.add(stop_id)
      if stop_id not in stops:
        dangling[key].append(stop_id)

  bad_coords, missing_name = [], []
  for stop_id, stop in stops.items():
    loc = stop.get('location') or {}
    lat, lng = loc.get('lat'), loc.get('lng')
    if not lat or not lng or not (
            22.1 < lat < 22.6) or not (
            113.8 < lng < 114.5):
      bad_coords.append(stop_id)
    name = stop.get('name') or {}
    if not name.get('zh') or not name.get('en'):
      missing_name.append(stop_id)

  return {
      'routes_with_dangling_stops': {k: v for k, v in dangling.items()},
      'dangling_stop_refs': sum(len(v) for v in dangling.values()),
      'orphan_stops': sorted(set(stops) - referenced),
      'stops_outside_hk_bbox': sorted(bad_coords),
      'stops_missing_a_name': sorted(missing_name),
      'empty_routes': sorted(k for k, r in routes.items() if not stop_ids(r)),
  }


def diff(old, new):
  old_routes, new_routes = old['routeList'], new['routeList']
  old_stops, new_stops = old['stopList'], new['stopList']

  removed_keys = sorted(set(old_routes) - set(new_routes))
  added_keys = sorted(set(new_routes) - set(old_routes))
  renamed, paired_new = pair_renames(
      removed_keys, added_keys, old_routes, new_routes)
  paired_old = {old_key for old_key, _ in renamed}

  def route_brief(routes, key):
    route = routes[key]
    return {
        'key': key,
        'route': route.get('route'),
        'co': route.get('co'),
        'serviceType': route.get('serviceType'),
        'orig': route.get('orig') or {},
        'dest': route.get('dest') or {},
        'stops': len(stop_ids(route)),
    }

  routes_changed = []
  for key in sorted(set(old_routes) & set(new_routes)):
    changes = compare_route(
        old_routes[key],
        new_routes[key],
        old_stops,
        new_stops)
    if changes:
      entry = route_brief(new_routes, key)
      entry['changes'] = changes
      routes_changed.append(entry)
  for old_key, new_key in renamed:
    entry = route_brief(new_routes, new_key)
    entry['previous'] = route_brief(old_routes, old_key)
    entry['changes'] = compare_route(old_routes[old_key], new_routes[new_key],
                                     old_stops, new_stops)
    routes_changed.append(entry)

  stops_added = sorted(set(new_stops) - set(old_stops))
  stops_removed = sorted(set(old_stops) - set(new_stops))
  stops_renamed, stops_moved = [], []
  for stop_id in sorted(set(old_stops) & set(new_stops)):
    before, after = old_stops[stop_id], new_stops[stop_id]
    bn, an = (before.get('name') or {}), (after.get('name') or {})
    if bn != an:
      stops_renamed.append({
          'id': stop_id,
          'before': bn.get('zh') or bn.get('en'),
          'after': an.get('zh') or an.get('en'),
      })
    bl, al = (before.get('location') or {}), (after.get('location') or {})
    if bl and al and bl != al:
      moved = metres(bl, al)
      if moved >= MOVED_STOP_METRES:
        stops_moved.append({
            'id': stop_id,
            'name': an.get('zh') or an.get('en'),
            'metres': round(moved),
        })

  return {
      'routes': {
          'added': [route_brief(new_routes, k) for k in added_keys if k not in paired_new],
          'removed': [route_brief(old_routes, k) for k in removed_keys if k not in paired_old],
          'changed': routes_changed,
          'total_before': len(old_routes),
          'total_after': len(new_routes),
      },
      'stops': {
          'added': [{'id': s, 'name': name_of(new_stops, s)} for s in stops_added],
          'removed': [{'id': s, 'name': name_of(old_stops, s)} for s in stops_removed],
          'renamed': stops_renamed,
          'moved': sorted(stops_moved, key=lambda s: -s['metres']),
          'total_before': len(old_stops),
          'total_after': len(new_stops),
      },
      'holidays': {
          'added': sorted(set(new.get('holidays') or []) - set(old.get('holidays') or [])),
          'removed': sorted(set(old.get('holidays') or []) - set(new.get('holidays') or [])),
      },
      'health': health(new),
      'health_before': health(old),
  }


HKT = timezone(timedelta(hours=8))

# Operator colours are the real liveries, so the accent bar on each row tells
# you who runs the route before you read a word.
OPERATORS = {
    'kmb': ('九巴', 'KMB', '#C8102E'),
    'ctb': ('城巴', 'CTB', '#F1AD00'),
    'nlb': ('嶼巴', 'NLB', '#00875A'),
    'gmb': ('小巴', 'GMB', '#5FB335'),
    'lrtfeeder': ('港鐵巴士', 'MTR BUS', '#9C2B8A'),
    'lightRail': ('輕鐵', 'LIGHT RAIL', '#8C6FD6'),
    'mtr': ('港鐵', 'MTR', '#E2564D'),
    'sunferry': ('新渡輪', 'SUN FERRY', '#2F86C5'),
    'fortuneferry': ('富裕', 'FORTUNE', '#2F86C5'),
    'hkkf': ('港九小輪', 'HKKF', '#2F86C5'),
}
FALLBACK_OPERATOR = ('其他', 'OTHER', '#8A9098')

MAX_NAMES_INLINE = 6
# Telegram counts a message in UTF-16 code units, so an emoji costs two where
# Python's len() charges one. Measure the way Telegram does.
MAX_TELEGRAM_CHARS = 4096


def operator(codes):
  for code in codes or []:
    if code in OPERATORS:
      return OPERATORS[code]
  return FALLBACK_OPERATOR


def natural_key(route_number):
  return [int(part) if part.isdigit() else part
          for part in re.split(r'(\d+)', route_number or '')]


def esc(value):
  return html.escape(str(value if value is not None else '—'))


def stop_names(stops):
  return [s.get('name') or s.get('id') for s in stops]


def change_chips(changes):
  """One chip per change, in reading order: where it goes, then what stops,
  then the quieter table-level fields."""
  chips = []
  for change in changes:
    kind = change['type']
    if kind == 'terminus':
      label = '起點' if change['field'] == 'orig' else '終點'
      chips.append(
          ('terminus', f"{label} {
              esc(
                  change['before'])} → {
              esc(
                  change['after'])}"))
    elif kind == 'stop_added':
      chips.append(('added', '＋ ' + names_html(stop_names(change['stops']))))
    elif kind == 'stop_removed':
      chips.append(('removed', '－ ' + names_html(stop_names(change['stops']))))
    elif kind == 'stop_reordered':
      chips.append(('changed', f"站序重排 · {change['count']} 站"))
    elif kind == 'field':
      field = change['field']
      if field == 'freq':
        chips.append(('quiet', '班次時間表更新'))
      elif field == 'fares':
        chips.append(
            ('changed', f"票價 {
                esc(
                    change['before'])} → {
                esc(
                    change['after'])}"))
      elif field == 'faresHoliday':
        chips.append(
            ('changed', f"假日票價 {
                esc(
                    change['before'])} → {
                esc(
                    change['after'])}"))
      elif field == 'jt':
        chips.append(
            ('quiet', f"行車時間 {
                esc(
                    change['before'])}→{
                esc(
                    change['after'])} 分"))
      else:
        chips.append(
            ('quiet', f"{
                esc(field)} {
                esc(
                    change['before'])}→{
                esc(
                    change['after'])}"))
  return chips


def names_html(names):
  shown = [esc(n) for n in names[:MAX_NAMES_INLINE]]
  rest = len(names) - len(shown)
  text = '、'.join(shown)
  if rest > 0:
    text += f' <span class="more">+{rest}</span>'
  return text


def route_blocks(changed, added, removed):
  """Group every route event under its route number, so a whole-route
  reshuffle (all variants of CTB 6 renumbered at once) reads as one block
  instead of a dozen unrelated rows."""
  blocks = defaultdict(list)
  for entry in changed:
    blocks[entry.get('route') or '?'].append(('changed', entry))
  for entry in added:
    blocks[entry.get('route') or '?'].append(('added', entry))
  for entry in removed:
    blocks[entry.get('route') or '?'].append(('removed', entry))
  return sorted(blocks.items(), key=lambda kv: natural_key(kv[0]))


def render_route_block(route_number, entries):
  zh, en, colour = operator(entries[0][1].get('co'))
  rows = []
  for status, entry in entries:
    variant = esc(entry.get('serviceType'))
    if status == 'changed':
      loud = [c for c in change_chips(entry['changes']) if c[0] != 'quiet']
      quiet = [
          text for kind,
          text in change_chips(
              entry['changes']) if kind == 'quiet']
      previous = entry.get('previous') or {}
      was = ''
      if previous:
        was = (f'<div class="was">原 {esc((previous.get("orig") or {}).get("zh"))}'
               f' <span class="arrow">→</span> '
               f'{esc((previous.get("dest") or {}).get("zh"))}</div>')
      body = ''.join(
          f'<div class="chip {kind}">{text}</div>' for kind,
          text in loud)
      if quiet:
        body += f'<div class="chip quiet">{" · ".join(quiet)}</div>'
      rows.append(f'<div class="row"><div class="variant">{variant}</div>'
                  f'<div class="detail">{blind(entry)}{was}{body}</div></div>')
    else:
      verb = '新增路線 Route added' if status == 'added' else '刪除路線 Route removed'
      rows.append(f'<div class="row {status}"><div class="variant">{variant}</div>'
                  f'<div class="detail">{blind(entry)}'
                  f'<div class="chip {status}">{verb} · {entry.get("stops", 0)} 站</div>'
                  f'</div></div>')
  return (f'<article class="route" style="--op:{colour}">'
          f'<div class="plate"><span>{esc(route_number)}</span></div>'
          f'<div class="body"><div class="op">{zh} <em>{en}</em></div>{"".join(rows)}</div>'
          f'</article>')


def blind(entry):
  """Origin and destination the way a destination blind reads them:
  Chinese on top, English underneath."""
  orig, dest = entry.get('orig') or {}, entry.get('dest') or {}
  zh = f'{esc(orig.get("zh"))} <span class="arrow">→</span> {esc(dest.get("zh"))}'
  en = f'{esc(orig.get("en"))} <span class="arrow">→</span> {esc(dest.get("en"))}'
  return f'<div class="blind">{zh}</div><div class="blind-en">{en}</div>'


def tally(diff):
  routes, stops = diff['routes'], diff['stops']
  return [
      ('路線改動', 'ROUTES CHANGED', len(routes['changed'])),
      ('新增路線', 'ROUTES ADDED', len(routes['added'])),
      ('刪除路線', 'ROUTES REMOVED', len(routes['removed'])),
      ('新增車站', 'STOPS ADDED', len(stops['added'])),
      ('刪除車站', 'STOPS REMOVED', len(stops['removed'])),
      ('車站改名', 'STOPS RENAMED', len(stops['renamed'])),
      ('車站移位', 'STOPS MOVED', len(stops['moved'])),
  ]


def stop_section(diff):
  stops = diff['stops']
  parts = []
  if stops['renamed']:
    parts.append(
        list_block(
            '改名 RENAMED', [
                f'{
                    esc(
                        s["before"])} < span class ="arrow" >→< / span > {
                    esc(
                        s["after"])} < code > {
                    esc(
                        s["id"])} < /code > ' for s in stops['renamed']]))
  if stops['moved']:
    parts.append(list_block('移位 MOVED', [
        f'{esc(s["name"])} <span class="metres">{s["metres"]} m</span> <code>{esc(s["id"])}</code>'
        for s in stops['moved']]))
  if stops['added']:
    parts.append(
        list_block(
            '新增 ADDED', [
                f'{
                    esc(
                        s["name"])} < code > {
                    esc(
                        s["id"])} < /code > ' for s in stops['added']]))
  if stops['removed']:
    parts.append(
        list_block(
            '刪除 REMOVED', [
                f'{
                    esc(
                        s["name"])} < code > {
                    esc(
                        s["id"])} < /code > ' for s in stops['removed']]))
  return ''.join(parts)


def list_block(title, items, limit=40):
  shown=items[: limit]
  rest = items[limit:]
  body=''.join(f'<li>{item}</li>' for item in shown)
  more=(
      f'< details > <summary > 另外 {
          len(rest)} 項 · {
          len(rest)} more < /summary > <ul >' +
      ''.join(
          f'<li>{item}</li>' for item in rest) +
      '</ul></details>') if rest else ''
  return f'<div class="block"><h3>{title}</h3><ul>{body}</ul>{more}</div>'


def health_section(health):
  rows=[
      ('無效站號引用', 'DANGLING STOP REFS', health['dangling_stop_refs'],
       [f'<code>{esc(k)}</code> → {", ".join(esc(s) for s in v)}'
        for k, v in health['routes_with_dangling_stops'].items()]),
      ('無路線經過的車站', 'ORPHAN STOPS', len(health['orphan_stops']),
       [f'<code>{esc(s)}</code>' for s in health['orphan_stops']]),
      ('座標不在香港範圍', 'COORDS OUTSIDE HK', len(health['stops_outside_hk_bbox']),
       [f'<code>{esc(s)}</code>' for s in health['stops_outside_hk_bbox']]),
      ('缺中文或英文站名', 'MISSING A NAME', len(health['stops_missing_a_name']),
       [f'<code>{esc(s)}</code>' for s in health['stops_missing_a_name']]),
      ('無車站的路線', 'EMPTY ROUTES', len(health['empty_routes']),
       [f'<code>{esc(s)}</code>' for s in health['empty_routes']]),
  ]
  out=[]
  for zh, en, count, items in rows:
    state='ok' if count == 0 else 'flag'
    detail=(f'<details><summary>展開 · show</summary><ul>' +
              ''.join(f'<li>{i}</li>' for i in items[: 200]) +
              '</ul></details>') if items else ''
    out.append(f'<div class="check {state}"><div class="count">{count}</div>'
               f'<div class="what">{zh} <em>{en}</em></div>{detail}</div>')
  return ''.join(out)


CSS="""
:root {
  --ground:#0E1012; --panel:#171A1D; --sunk:#101315;
  --ink:#EDEAE3; --muted:#8A9098; --rule:#262A2E;
  --added:#4FB477; --removed:#E2564D; --changed:#F1AD00; --quiet:#6E757C;
  --plate:#EDEAE3;
  --zh:"PingFang HK","Noto Sans HK","Hiragino Sans CNS","Microsoft JhengHei",sans-serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,var(--zh);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.5;padding:0 0 4rem;-webkit-text-size-adjust:100%}
.wrap{max-width:60rem;margin:0 auto;padding:0 1rem}
a{color:var(--ink)}
code{font-family:var(--mono);font-size:.78em;color:var(--muted);word-break:break-all}
em{font-style:normal;font-family:var(--mono);font-size:.72em;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted)}
.arrow{color:var(--muted);padding:0 .15em}

header{border-bottom:1px solid var(--rule);padding:2.5rem 0 1.5rem;margin-bottom:1.5rem}
.eyebrow{font-family:var(--mono);font-size:.7rem;letter-spacing:.22em;text-transform:uppercase;
  color:var(--muted)}
h1{font-size:clamp(1.6rem,6vw,2.4rem);font-weight:650;letter-spacing:-.01em;margin:.4rem 0 .1rem}
h1 span{color:var(--muted);font-weight:400}
.stamp{font-family:var(--mono);font-size:.78rem;color:var(--muted);margin-top:.6rem;
  display:flex;flex-wrap:wrap;gap:.25rem 1.2rem}

/* The tally strip is the one loud element: a destination-blind band of counts. */
.tally{display:grid;grid-template-columns:repeat(auto-fit,minmax(7.2rem,1fr));
  background:var(--panel);border:1px solid var(--rule);border-radius:3px;overflow:hidden;
  margin-bottom:2.5rem}
.tally>div{padding:.9rem 1rem;border-right:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.tally .n{font-family:var(--mono);font-size:1.75rem;font-weight:600;line-height:1;
  font-variant-numeric:tabular-nums}
.tally .n.zero{color:var(--quiet)}
.tally .l{font-size:.75rem;color:var(--muted);margin-top:.35rem}
.tally .l em{display:block;margin-top:.1rem}

h2{font-size:.78rem;font-family:var(--mono);letter-spacing:.2em;text-transform:uppercase;
  color:var(--muted);padding-bottom:.5rem;border-bottom:1px solid var(--rule);margin:2.5rem 0 1rem}
h3{font-size:.72rem;font-family:var(--mono);letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin:1.25rem 0 .4rem}

.route{display:flex;gap:.85rem;padding:1rem 0;border-bottom:1px solid var(--rule)}
/* Route plate: 2px box and boxy corners, the way a route number is printed
   on a Hong Kong stop pole. */
.plate{flex:0 0 auto}
.plate span{display:inline-block;min-width:3.4rem;text-align:center;
  font-family:var(--mono);font-weight:700;font-size:1rem;letter-spacing:.02em;
  padding:.3rem .4rem;color:var(--ground);background:var(--plate);
  border-radius:2px;border-bottom:3px solid var(--op)}
.body{min-width:0;flex:1}
.op{font-size:.8rem;color:var(--op);margin-bottom:.35rem}
.op em{color:var(--muted)}
.row{display:flex;gap:.7rem;padding:.4rem 0}
.row+.row{border-top:1px dashed var(--rule)}
.variant{flex:0 0 1.2rem;font-family:var(--mono);font-size:.72rem;color:var(--quiet);
  padding-top:.15rem}
.detail{min-width:0;flex:1}
.blind{font-size:.95rem;line-height:1.35;font-family:var(--zh)}
.blind-en{font-size:.76rem;line-height:1.3;color:var(--muted);margin-top:.05rem}
.row.removed .blind,.row.removed .blind-en{text-decoration:line-through;
  text-decoration-color:var(--removed)}
.was{font-size:.78rem;color:var(--muted);margin-top:.1rem}
.chip{font-size:.82rem;margin-top:.3rem;padding-left:.75rem;
  border-left:2px solid var(--quiet);color:var(--ink)}
.chip.added{border-color:var(--added)}
.chip.removed{border-color:var(--removed)}
.chip.changed,.chip.terminus{border-color:var(--changed)}
.chip.quiet{border-color:var(--rule);color:var(--muted)}
.more{color:var(--muted);font-family:var(--mono);font-size:.75em}

.block ul{list-style:none;padding:0}
.block li{padding:.3rem 0;border-bottom:1px solid var(--rule);font-size:.88rem}
details{margin:.5rem 0}
summary{cursor:pointer;font-size:.8rem;color:var(--muted);font-family:var(--mono)}
summary:focus-visible,a:focus-visible{outline:2px solid var(--changed);outline-offset:2px}

.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);border-radius:3px}
.check{background:var(--panel);padding:.9rem 1rem}
.check .count{font-family:var(--mono);font-size:1.4rem;font-weight:600;line-height:1}
.check.ok .count{color:var(--quiet)}
.check.flag .count{color:var(--changed)}
.check .what{font-size:.8rem;color:var(--muted);margin-top:.3rem}
.empty{padding:2rem 0;color:var(--muted)}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--rule);
  font-size:.78rem;color:var(--quiet)}
@media (max-width:480px){.route{gap:.6rem}.plate span{min-width:2.9rem;font-size:.9rem}}
"""


def render_html(diff, meta):
  counts=tally(diff)
  tally_html=''.join(
      f'<div><div class="n{"" if n else " zero"}">{n}</div>'
      f'<div class="l">{zh}<em>{en}</em></div></div>' for zh, en, n in counts)

  blocks=route_blocks(diff['routes']['changed'], diff['routes']['added'],
                        diff['routes']['removed'])
  routes_html=''.join(render_route_block(number, entries)
                        for number, entries in blocks)
  stops_html=stop_section(diff)
  quiet=not routes_html and not stops_html

  holidays=diff.get('holidays') or {}
  holiday_html=''
  if holidays.get('added') or holidays.get('removed'):
    holiday_html='<h2>公眾假期 Holidays</h2>' + list_block(
        '變更 CHANGED',
        [f'＋ {esc(d)}' for d in holidays['added']] +
        [f'－ {esc(d)}' for d in holidays['removed']])

  return f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>資料變更 · hk-bus-crawling</title>
<meta name="description" content="每日巴士資料變更摘要 · daily summary of Hong Kong public transport data changes">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">hkbus / hk-bus-crawling</div>
  <h1>資料變更 <span>Data changes</span></h1>
  <div class="stamp">
    <span>{esc(meta.get('generated'))} HKT</span>
    <span>{esc(meta.get('before_label'))} <span class="arrow">→</span> {esc(meta.get('after_label'))}</span>
    <span>{diff['routes']['total_after']} 路線 · {diff['stops']['total_after']} 車站</span>
  </div>
</header>

<div class="tally">{tally_html}</div>

{'<div class="empty">今次更新無資料變更。<br><em>No route or stop changes in this run.</em></div>' if quiet else ''}
{f'<h2>路線 Routes</h2>{routes_html}' if routes_html else ''}
{f'<h2>車站 Stops</h2>{stops_html}' if stops_html else ''}
{holiday_html}

<h2>資料檢查 Data health</h2>
<div class="checks">{health_section(diff['health'])}</div>

<footer>
  由 <a href="https://github.com/hkbus/hk-bus-crawling">hkbus/hk-bus-crawling</a> 自動產生 ·
  generated from <code>routeFareList.min.json</code>
</footer>
</div>
</body>
</html>
"""


def health_deltas(diff):
  before, after=diff.get('health_before') or {}, diff['health']

  def count(source, key):
    value=(source or {}).get(key)
    return value if isinstance(value, int) else len(value or ())
  return [(label, count(after, key), count(before, key)) for label, key in (
      ('無效站號', 'dangling_stop_refs'),
      ('孤立車站', 'orphan_stops'),
      ('座標異常', 'stops_outside_hk_bbox'),
      ('缺站名', 'stops_missing_a_name'),
      ('無站路線', 'empty_routes'),
  )]


def has_changes(diff):
  """True when this crawl moved something a reader would care about. Drives
  whether a Telegram message is written at all - most runs change nothing,
  and a bot that posts 'no changes' twice a day gets muted."""
  return any([
      diff['routes']['added'], diff['routes']['removed'], diff['routes']['changed'],
      diff['stops']['added'], diff['stops']['removed'],
      diff['stops']['renamed'], diff['stops']['moved'],
      diff['holidays']['added'], diff['holidays']['removed'],
      any(after > before for _, after, before in health_deltas(diff)),
  ])


def tg(value):
  """Telegram's HTML parse mode rejects a message with a stray ``&``, and 35
  stop names carry one (``HK Zoological & Botanical Garden``)."""
  return html.escape(str(value if value is not None else '-'), quote = False)


def telegram_length(text):
  """Length in UTF-16 code units, which is what Telegram's 4096 limit counts."""
  return len(text.encode('utf-16-le')) // 2


def render_telegram(diff, meta):
  routes, stops=diff['routes'], diff['stops']
  lines=[f"<b>🚌 巴士資料更新</b> <code>{tg(meta.get('after_label'))}</code>"]

  counts=[f'{zh} {n}' for zh, _, n in tally(diff) if n]
  lines.append(' · '.join(counts) if counts else '今次更新無資料變更。')

  blocks=route_blocks(routes['changed'], routes['added'], routes['removed'])
  if blocks:
    lines.append('')
    for number, entries in blocks:
      zh, _, _=operator(entries[0][1].get('co'))
      summaries=[]
      for status, entry in entries:
        if status == 'added':
          summaries.append((1, '✨ 新增'))
        elif status == 'removed':
          summaries.append((1, '🗑 刪除'))
        else:
          for change in entry['changes']:
            if change['type'] == 'terminus':
              summaries.append(
                  (0, f"🚩 {tg(change['before'])}→{tg(change['after'])}"))
            elif change['type'] == 'stop_added':
              summaries.append((2, '➕ ' + '、'.join(tg(n)
                                                   for n in stop_names(change['stops'])[:3])))
            elif change['type'] == 'stop_removed':
              summaries.append((2, '➖ ' + '、'.join(tg(n)
                                                   for n in stop_names(change['stops'])[:3])))
      deduped=list(
          dict.fromkeys(
              text for _,
              text in sorted(
                  summaries,
                  key=lambda s: s[0])))
      if deduped:
        lines.append(f"<b>{tg(number)}</b> <i>{zh}</i> " +
                     '; '.join(deduped[:4]))

  if stops['renamed']:
    lines.append('')
    lines.append('✏️ 改名: ' + '、'.join(
        f"{tg(s['before'])}→{tg(s['after'])}" for s in stops['renamed'][:5]))

  # Only shout when data health got WORSE this run - the standing counts are
  # on the page, and a bot that warns every run gets muted.
  flagged = [f'{label} +{after - before}'
             for label, after, before in health_deltas(diff) if after > before]
  if flagged:
    lines.append('')
    lines.append('⚠️ ' + ' · '.join(flagged))

  if meta.get('page_url'):
    lines.append('')
    lines.append(f"🔗 <a href=\"{tg(meta['page_url'])}\">完整摘要</a>")

  message = '\n'.join(lines)
  if telegram_length(message) > MAX_TELEGRAM_CHARS:
    tail = f"\n\n… 太長，請看 🔗 {tg(meta.get('page_url', ''))}"
    budget = MAX_TELEGRAM_CHARS - telegram_length(tail)
    kept = []
    used = 0
    for line in message.split('\n'):
      cost = telegram_length(line) + 1
      if used + cost > budget:
        break
      kept.append(line)
      used += cost
    message = '\n'.join(kept) + tail
  return message


def write_summary(before, after, html_path=None, json_path=None,
                  telegram_path=None, before_label='data.hkbus.app',
                  after_label='this run',
                  page_url='https://data.hkbus.app/summary.html'):
  """Compare two snapshots and write whichever outputs were asked for.

  The Telegram file is written only when something actually changed, so the
  caller can post it unconditionally if the file exists.
  """
  result = diff(before, after)
  meta = {
      'generated': datetime.now(HKT).strftime('%Y-%m-%d %H:%M'),
      'before_label': before_label,
      'after_label': after_label,
      'page_url': page_url,
  }
  if json_path:
    with open(json_path, 'w', encoding='utf-8') as f:
      json.dump(result, f, ensure_ascii=False, indent=1)
  if html_path:
    with open(html_path, 'w', encoding='utf-8') as f:
      f.write(render_html(result, meta))
  if telegram_path and has_changes(result):
    with open(telegram_path, 'w', encoding='utf-8') as f:
      f.write(render_telegram(result, meta))
  return result


def load(path):
  with open(path, encoding='utf-8') as f:
    return json.load(f)


def main():
  parser = argparse.ArgumentParser(
      description=__doc__,
      formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument(
      'previous',
      nargs='?',
      help='routeFareList.min.json before this crawl')
  parser.add_argument(
      'current',
      nargs='?',
      help='routeFareList.min.json after this crawl')
  parser.add_argument(
      '--json',
      dest='json_out',
      help='write the raw comparison here')
  parser.add_argument('--html', help='write the summary page here')
  parser.add_argument(
      '--telegram',
      help='write the Telegram message here, only when something changed')
  parser.add_argument('--before-label', default='previous')
  parser.add_argument('--after-label', default='current')
  parser.add_argument(
      '--page-url',
      default='https://hkbus.github.io/hk-bus-crawling/summary.html')
  args = parser.parse_args()

  result = write_summary(
      load(
          args.previous),
      load(
          args.current),
      html_path=args.html,
      json_path=args.json_out,
      telegram_path=args.telegram,
      before_label=args.before_label,
      after_label=args.after_label,
      page_url=args.page_url)
  if not any((args.json_out, args.html, args.telegram)):
    print(render_telegram(result, {'after_label': args.after_label,
                                   'page_url': args.page_url}))
  return 0


if __name__ == '__main__':
  sys.exit(main())
