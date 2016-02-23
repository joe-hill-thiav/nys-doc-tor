#!/env python

import argparse
import csv
from datetime import datetime
import re
import string
import time

from bs4 import BeautifulSoup

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
MAX_PAGES = 400

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

	def __init__(self):
		""" fetch DFH_STATE_TOKEN from the search form """
		s = self._r(requests.get(URLS['base']), {'id': 'M00_LAST_NAMEI'})
		self.DFH_STATE_TOKEN = s.find(
			'input', attrs={'name': 'DFH_STATE_TOKEN'}
		)['value']

	def _process_page(self, page):
		page_data = {}

		for i, row in enumerate(page.find(id='dinlist').find_all('tr')):
			if i == 0:
				continue

			din = row.find_all('td')[0].find(class_='buttolink')['value']

			name = row.find_all('td')[1].text.strip()
			log.debug(" - {0}".format(name))

			page_data[din] = {
				'din': din,
				'name': name,
				'sex': row.find_all('td')[2].text.strip(),
				'dob': row.find_all('td')[3].text.strip(),
				'status': row.find_all('td')[4].text.strip(),
				'facility': row.find_all('td')[5].text.strip(),
				'ethnicity': row.find_all('td')[6].text.strip(),
			}
			last_seen_name = name

			k02 = row.find_all('td')[0].find(name='K02')

			self.headers['Referer'] = URLS['search']

			data = {
				'M13_PAGE_CLICKI': '',
				'M13_SEL_DINI': din,
				'K01': 'WINQ130',
				'K02': k02,
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

			page_data[din]['county'] = s.find(headers='t1k').text.strip()

		return page_data, last_seen_name

	def search(self, name, page_i=0):
		# TODO support other types of query
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

		page_data, last_seen_name = self._process_page(s)

		if last_seen_name == name:
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

			_page_data, last_seen_name = self._process_page(s)
			page_data.update(_page_data)

		if len(page_data) > 3:
			if page_i < MAX_PAGES:
				# all pages except the last have 5 results
				page_i += len(page_data) / 4
				try:
					_d = self.search(last_seen_name, page_i)
					page_data.update(_d[0])
					last_seen_name = _d[1]
					page_i = _d[2]
				except ConnectionError as e:
					log.error("Connection error, {0}".format(e))
					pass
				except KeyboardInterrupt:
					log.error("User interrupted")
					pass

			else:
				log.warning('reached MAX_PAGES ({0})'.format(MAX_PAGES))

		return page_data, last_seen_name, page_i


nys = NYS()

parser = argparse.ArgumentParser()
parser.add_argument('--start', default='a')
args = parser.parse_args()

data = {}

data, last_seen_name, pages = nys.search(args.start)

f = open('{0:%Y-%m-%d}_{1}-{2}.csv'.format(datetime.now(), args.start, last_seen_name), "w")

log.info('Fetched {0} pages ({1} records). Last name "{2}"'.format(
	pages, len(data), last_seen_name)
)

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
log.info('Wrote CSV')
