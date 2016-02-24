#!/env python

import argparse
import csv
from collections import OrderedDict
from datetime import datetime
import random
import re
import string
import time

from bs4 import BeautifulSoup

from multiprocessing.dummy import Pool
from multiprocessing import cpu_count

from zenlog import log

import requests
from requests.exceptions import ConnectionError
import requests_cache

# requests logging spew
#import logging
#import httplib
#httplib.HTTPConnection.debuglevel = 1
#logging.basicConfig()
#logging.getLogger().setLevel(logging.DEBUG)
#requests_log = logging.getLogger("requests.packages.urllib3")
#requests_log.setLevel(logging.DEBUG)
#requests_log.propagate = True


URLS = {
	'base': 'http://nysdoccslookup.doccs.ny.gov/',
	'search': 'http://nysdoccslookup.doccs.ny.gov/GCA00P00/WIQ1/WINQ000',
	'detail': 'http://nysdoccslookup.doccs.ny.gov/GCA00P00/WIQ3/WINQ130',
	'detail2': 'http://nysdoccslookup.doccs.ny.gov/GCA00P00/WIQ2/WINQ120',
}

requests_cache.install_cache('scrape', allowable_methods=['GET', 'POST'])


class FUBAR(Exception):
	# the only appropriate way of describing how the site handles bad requests..
	pass


class NYS(object):
	headers = {
		'User-Agent': "Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:42.0) Gecko/20100101 Firefox/42.0",
		'Referer': URLS['base'],
		'Host': 'nysdoccslookup.doccs.ny.gov',
		'Accept-Language': 'en-GB,en;q=0.5',
		'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
		'Pragma': 'no-cache',
		'Cache-Control': 'no-cache',
	}

	_seeds = None

	def _r(self, response, expected_element=None):
		""" BeautifulSoup response.content, check for expected_element """
		s = BeautifulSoup(response.content, "lxml")

		if expected_element is not None and s.find(**expected_element) is None:
			f = open('{0}.html'.format(time.time()), "w")
			f.write(response.content)
			f.close()
			print response.request.headers
			print response.headers
			from pdb import set_trace; set_trace() 
			raise FUBAR("Failed to load page")
		return s

	def __init__(self, limit=10):
		""" fetch DFH_STATE_TOKEN from the search form """
		s = self._r(requests.get(URLS['base']), {'id': 'M00_LAST_NAMEI'})
		self.DFH_STATE_TOKEN = s.find(
			'input', attrs={'name': 'DFH_STATE_TOKEN'}
		)['value']

		self.limit = limit

		self.pool = Pool(cpu_count() * 2)

	def inmate_details(self, din):
			self.headers['Referer'] = URLS['search']

			log.debug("DETAILS {0}".format(din))

			data = {
				'M13_PAGE_CLICKI': '',
				'M13_SEL_DINI': din,
				'K01': 'WINQ130',
				'K03': '',
				'K04': '1',
				'K05': '2',
				'K06': '1',
				'DFH_STATE_TOKEN': self.DFH_STATE_TOKEN,
				'DFH_MAP_STATE_TOKEN': ''
			}

			s = self._r(requests.post(URLS['detail'], data, headers=self.headers))

			if s.find(id='t1a') is None:
				# could be asking for confirmation
				data['K01'] = 'WINQ120'
				if s.find(value=din) is None:
					raise FUBAR("Failed to load page")
				data['K02'] = s.find(value=din).parent()[2]['value']
				data['din2'] = din
				del data['M13_SEL_DINI']
				del data['M13_PAGE_CLICKI']
				data['M12_SEL_DINI'] = din
				data['M12_PAGE_CLICKI'] = ''

				self.headers['Referer'] = URLS['detail']

				s = self._r(requests.post(URLS['detail2'], data, headers=self.headers),
										{'id': 't1a'})

			return {
				"county": s.find(headers='t1k').text.strip()
			}


	def _process_page(self, page):
		page_data = OrderedDict()

		for i, row in enumerate(page.find(id='dinlist').find_all('tr')):
			if i == 0:
				continue

			din = row.find_all('td')[0].find(class_='buttolink')['value']

			name = row.find_all('td')[1].text.strip()

			page_data[din] = {
				'din': din,
				'name': name,
				'sex': row.find_all('td')[2].text.strip(),
				'dob': row.find_all('td')[3].text.strip(),
				'status': row.find_all('td')[4].text.strip(),
				'facility': row.find_all('td')[5].text.strip(),
				'ethnicity': row.find_all('td')[6].text.strip(),
			}

		try:
			results = self.pool.map(self.inmate_details, page_data.keys())
		except KeyboardInterrupt:
			self.pool.terminate()
			raise

		for i, din in enumerate(page_data.keys()):
			page_data[din]['county'] = results[i]

		return page_data

	def search(self, name):
		if ',' not in name:
			name += ','

		log.info('SEARCH `name` = `{0}`'.format(name))

		self.headers['Referer'] = URLS['base']
		s = self._r(requests.post(URLS['search'], {
			'K01': 'WINQ000',
			'DFH_STATE_TOKEN': self.DFH_STATE_TOKEN,
			'DFH_MAP_STATE_TOKEN': '',
			'M00_LAST_NAMEI': name.split(',')[0].strip(),
			'M00_FIRST_NAMEI': name.split(',')[1].strip(), 
			'M00_MID_NAMEI': '',
			'M00_NAME_SUFXI': '',
			'M00_DOBCCYYI': '',
			'M00_DIN_FLD1I': '',
			'M00_DIN_FLD2I': '',
			'M00_DIN_FLD3I': '',
			'M00_NYSID_FLD1I': '',
			'M00_NYSID_FLD2I': '',
		}, headers=self.headers), {'id': 'dinlist'})

		page_data = self._process_page(s), s

		return page_data

	@property
	def seeds(self):
		if self._seeds:
			return self._seeds

		seeds = []
		while len(seeds) < self.limit:
			seeds.append(''.join(random.choice(string.uppercase) for x in range(2)))

		self._seeds = seeds

		return seeds

	def get_random_records(self):
		data = {}

		for seed in self.seeds:
			if len(data) >= self.limit:
				break
			d = self.search(seed)
			data.update(d[0])

		return data

	def get_all_records(self, start):
		data, s = self.search(start)

		def _names(data):
			return [data[k]['name'] for k in [data.keys()[0], data.keys()[-1]]]

		names = _names(data)

		if len(data) > 3:
				# all pages except the last have 5 results
				try:
					_data, s = self.get_all_records(names[-1])
					data.update(_data)
					names = _names(_data)
				except ConnectionError as e:
					log.error("Connection error, {0}".format(e))
					pass
				except KeyboardInterrupt:
					log.error("User interrupted")
					pass

		while names[0] == names[-1]:
			# probably a single person with > 3 records; searching by their name again
			# won't help. we have to click "Next"
			form_next = s.find('div', class_='aligncenter').parent.parent
			k02 = form_next.find('input', attrs={"name": "K02"})['value']

			log.info(" - fetching additional page")

			if len(page_data) > 3:
				self.headers['Referer'] = URLS['search']
			else:
				self.headers['Referer'] = URLS['base']

			s = self._r(requests.post(URLS['detail'], {
				'M13_PAGE_CLICKI': 'Y',
				'M13_SEL_DINI': '',
				'K01': 'WINQ130',
				'K02': k02,
				'K03': '',
				'K04': '1',
				'K05': '2',
				'K06': '1',
				'DFH_STATE_TOKEN': self.DFH_STATE_TOKEN,
				'DFH_MAP_STATE_TOKEN': '',
				'next': "Next 4 Inmate Names"
			}, headers=self.headers), {'id': 'dinlist'})

			_data = self._process_page(s)
			data.update(_page_data)
			names = _names(_data)

		return data, s


def writeCSV(data, filename):
	with open(filename, "w") as f:
		writer = csv.writer(f)

		FIELDS = [
			'din',
			'name',
			'sex',
			'dob',
			'facility',
			'ethnicity',
			'status',
			'county'
		]

		writer.writerow(FIELDS)

		for d in sorted(data.keys()):
			writer.writerow([data[d][field] for field in FIELDS])

		f.close()
		log.info('Wrote {0}'.format(filename))


# define argparse arguments

parser = argparse.ArgumentParser()

parser.add_argument(
	'--limit', default=20,
	help=u'Stop after this many records (default 20)'
)

group = parser.add_mutually_exclusive_group(required=True)
group.add_argument(
	'--start', help=u'Start with a search for this name'
)
group.add_argument(
	'--random', action='store_true', help=u'Fetch at random'
)
group.add_argument(
	'--seed-file', type=argparse.FileType('r'),
	help=u'Use this list of names to search'
)
group.add_argument(
	'--generate-seeds',
	action='store_true',
	help=u'Output list of random bigrams for repeatable random searches'
)
group.add_argument('--din', help='Get details on a single inmate by DIN')

args = parser.parse_args()

# argument sanity checks

if args.din and args.random:
	parser.error('--random and --din cannot both be specified')

# initialise API object

nys = NYS(limit=args.limit)

# perform actions

if args.seed_file:
	args.random = True
	nys._seeds = [s.strip() for s in args.seed_file.readlines()]

if args.din:
	print nys.inmate_details(args.din)
elif args.generate_seeds:
	for seed in nys.seeds:
		print seed
elif args.random or args.seed_file:
	writeCSV(nys.get_random_records(),
			'{0:%Y-%m-%d}_random.csv'.format(datetime.now()))
else:
	d = nys.get_all_records(args.start)[0]
	names = [d[k]['name'].split(',')[0] for k in [d.keys()[0], d.keys()[-1]]]
	writeCSV(d,
			'{0:%Y-%m-%d}_{1}-{2}.csv'.format(
				datetime.now(),
				*names
			))
