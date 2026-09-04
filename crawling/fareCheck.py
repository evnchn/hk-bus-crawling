# -*- coding: utf-8 -*-
# Report KMB fares that disagree with KMB's own published per-stop AirFare.
# Report only: this never modifies routeFareList.

import asyncio
import json
import logging

import httpx

from crawl_utils import emitRequest, get_request_limit

logger = logging.getLogger(__name__)

BOUND = {'O': 1, 'I': 2}
HEADERS = {'User-Agent': 'Mozilla/5.0'}


def stopCode(name):
  # trailing "(KT460)" -> KT460
  if name.endswith(')') and '(' in name:
    return name[name.rindex('(') + 1:-1]
  return None


async def checkRoute(key, route, bound, stopList, client, semaphore):
  url = ('https://search.kmb.hk/KMBWebSite/Function/FunctionRequest.ashx'
         '?action=getstops&route=%s&bound=%d&serviceType=%02d'
         % (route['route'], BOUND[bound], int(route.get('serviceType', 1))))
  async with semaphore:
    r = await emitRequest(url, client, HEADERS)
  rows = r.json()['data']['routeStops']

  stops = route['stops']['kmb']
  # only compare when both sides describe the same stop sequence
  if len(rows) != len(stops):
    return {'skipped': 'length'}
  theirs = [stopCode(row['CName']) for row in rows]
  ours = [stopCode(stopList[s]['name']['zh']) for s in stops]
  if any(a and b and a != b for a, b in zip(theirs, ours)):
    return {'skipped': 'stopCode'}

  diffs, unpriced, unnamed = [], 0, 0
  for i in range(min(len(route['fares']), len(rows) - 1)):
    if theirs[i] is None:
      unnamed += 1
    try:
      ours_fare = float(route['fares'][i])
      theirs_fare = float(rows[i]['AirFare'])
    except (TypeError, ValueError):
      continue
    # AirFare 0 means KMB publishes no fare for that boarding stop
    if theirs_fare == 0:
      unpriced += 1
      continue
    if abs(ours_fare - theirs_fare) > 0.001:
      diffs.append({'seq': i, 'stop': theirs[i],
                    'ours': ours_fare, 'kmb': theirs_fare})
  return {'key': key, 'route': route['route'], 'bound': bound,
          'serviceType': route.get('serviceType'), 'diffs': diffs,
          'unpricedByKmb': unpriced, 'stopsWithoutCode': unnamed}


async def fareCheck():
  with open('routeFareList.min.json', 'r', encoding='UTF-8') as f:
    db = json.load(f)
  routeList, stopList = db['routeList'], db['stopList']

  targets = []
  for key, route in routeList.items():
    if route.get('co') != ['kmb'] or not route.get('fares'):
      continue
    bound = route.get('bound', {}).get('kmb')
    if bound in BOUND:
      targets.append((key, route, bound))

  semaphore = asyncio.Semaphore(get_request_limit())
  async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, pool=None)) as client:
    results = await asyncio.gather(*[
        checkRoute(key, route, bound, stopList, client, semaphore)
        for key, route, bound in targets], return_exceptions=True)

  mismatches, skipped, failed = [], {'length': 0, 'stopCode': 0}, 0
  compared = unpriced = unnamed = 0
  for result in results:
    if isinstance(result, BaseException):
      failed += 1
      logger.warning(f"fare check failed: {repr(result)}")
    elif 'skipped' in result:
      skipped[result['skipped']] += 1
    else:
      compared += 1
      unpriced += result['unpricedByKmb']
      unnamed += result['stopsWithoutCode']
      if result['diffs']:
        mismatches.append(result)

  stopLevel = sum(len(m['diffs']) for m in mismatches)
  logger.info(f"KMB fare check: {len(mismatches)} of {compared} compared route "
              f"directions disagree with KMB, {stopLevel} stops")
  logger.info(f"  not compared: {skipped['length']} stop count, "
              f"{skipped['stopCode']} stop codes, {failed} failed; "
              f"not checked within compared routes: {unpriced} stops unpriced "
              f"by KMB, {unnamed} stops without a code")
  for m in sorted(mismatches, key=lambda m: -len(m['diffs']))[:10]:
    d = m['diffs'][0]
    logger.info(f"  {m['route']} {m['bound']} serviceType={m['serviceType']}: "
                f"{len(m['diffs'])} stops, first {d['stop']} "
                f"ours={d['ours']} kmb={d['kmb']}")

  with open('fareMismatch.json', 'w', encoding='UTF-8') as f:
    json.dump({'targets': len(targets), 'compared': compared,
               'skippedStopCount': skipped['length'],
               'skippedStopCode': skipped['stopCode'], 'failed': failed,
               'stopsUnpricedByKmb': unpriced, 'stopsWithoutCode': unnamed,
               'mismatches': sorted(mismatches, key=lambda m: m['key'])},
              f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  logging.getLogger('httpx').setLevel(logging.WARNING)
  try:
    asyncio.run(fareCheck())
  except Exception as e:
    # report-only: never fail the crawl that publishes the data
    logging.getLogger(__name__).error(f"fare check skipped: {repr(e)}")
