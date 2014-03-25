#!/usr/bin/env python
"""
Script to add ReleaseFile objects for Python releases on the new pydotorg.
To use (RELEASE is something like 3.3.5rc2):

* Copy this script to dinsdale (it needs access to all the release files).
  You could also download all files, then you need to adapt the "ftp_root"
  string below.

* Make sure all download files are in place in the correct /data/ftp.python.org
  subdirectory.

* Create a new Release object via the Django admin (adding via API is
  currently broken), the name MUST be "Python RELEASE".

* Put an AUTH_INFO variable containing "username:api_key" in your environment.

* Call this script as "python add-to-pydotorg.py RELEASE".

  Each call will remove all previous file objects, so you can call the script
  multiple times.

Georg Brandl, March 2014.
"""

import os
import re
import sys
import json
import time
import hashlib
from os import path

import requests

try:
    auth_info = os.environ['AUTH_INFO']
except KeyError:
    print 'Please set an environment variable named AUTH_INFO ' \
        'containing "username:api_key".'
    sys.exit()

base_url = 'https://www.python.org/api/v1/'
ftp_root = '/data/ftp.python.org/pub/python/'
download_root = 'https://www.python.org/ftp/python/'

headers = {'Authorization': 'ApiKey %s' % auth_info, 'Content-Type': 'application/json'}

rx = re.compile
# value is (file "name", OS id, file "description")
file_descriptions = [
    (rx(r'\.tgz$'),              ('Gzipped source tarball', 3, '')),
    (rx(r'\.tar\.xz$'),          ('XZ compressed source tarball', 3, '')),
    (rx(r'\.amd64\.msi$'),       ('Windows x86-64 MSI installer', 1,
                                  'for AMD64/EM64T/x64, not Itanium processors')),
    (rx(r'\.msi$'),              ('Windows x86 MSI installer', 1, '')),
    (rx(r'\.chm$'),              ('Windows help file', 1, '')),
    (rx(r'amd64-pdb\.zip$'),     ('Windows debug information files for 64-bit binaries', 1, '')),
    (rx(r'-pdb\.zip$'),          ('Windows debug information files', 1, '')),
    (rx(r'-macosx10\.5(_rev\d)?\.dmg$'),  ('Mac OS X 32-bit i386/PPC installer', 2,
                                  'for Mac OS X 10.5 and later')),
    (rx(r'-macosx10\.6(_rev\d)?\.dmg$'),  ('Mac OS X 64-bit/32-bit installer', 2,
                                  'for Mac OS X 10.6 and later')),
]

def changelog_for(release):
    new_url = 'http://docs.python.org/release/%s/whatsnew/changelog.html' % release
    if requests.head(new_url).status_code != 200:
        return 'http://hg.python.org/cpython/file/v%s/Misc/NEWS' % release

def slug_for(release):
    return release[0] + '-' + release[2] + '-' + release[4] + \
        ('-' + release[5:] if release[5:] else '')

def sigfile_for(release, rfile):
    return download_root + '%s/%s.asc' % (release, rfile)

def md5sum_for(release, rfile):
    return hashlib.md5(open(ftp_root + release[:5] + '/' + rfile, 'rb').read()).hexdigest()

def filesize_for(release, rfile):
    return path.getsize(ftp_root + release[:5] + '/' + rfile)

def make_slug(text):
    return re.sub('[^a-zA-Z0-9_-]', '', text.replace(' ', '-'))

def build_release_dict(release, reldate, is_latest, page_pk):
    """Return a dictionary with all needed fields for a Release object."""
    return dict(
        name = 'Python ' + release,
        slug = slug_for(release),
        version = int(release[0]),
        is_published = True,
        release_date = reldate,  # in "YYYY-MM-ddTHH:MM:SS"
        pre_release = bool(release[5:]),
        release_page = '/api/v1/pages/page/%s/' % page_pk, # XXX doesn't work yet
        release_notes_url = changelog_for(release),
        show_on_download_page = True,
    )

def build_file_dict(release, rfile, rel_pk, file_desc, os_pk, add_desc):
    """Return a dictionary with all needed fields for a ReleaseFile object."""
    return dict(
        name = file_desc,
        slug = slug_for(release) + '-' + make_slug(file_desc)[:40],
        os = '/api/v1/downloads/os/%s/' % os_pk,
        release = '/api/v1/downloads/release/%s/' % rel_pk,
        description = add_desc,
        is_source = os_pk == 3,
        url = download_root + '%s/%s' % (release[:5], rfile),
        gpg_signature_file = sigfile_for(release[:5], rfile),
        md5_sum = md5sum_for(release, rfile),
        filesize = filesize_for(release, rfile),
        download_button = 'tar.xz' in rfile or
                          'macosx10.6.dmg' in rfile or
                          ('.msi' in rfile and not 'amd64' in rfile),
    )

def list_files(release):
    """List all of the release's download files."""
    reldir = release[:5]
    for rfile in os.listdir(path.join(ftp_root, reldir)):
        if not path.isfile(path.join(ftp_root, reldir, rfile)):
            continue
        if rfile.endswith('.asc'):
            continue
        for prefix in ('python', 'Python'):
            if rfile.startswith(prefix):
                break
        else:
            print '    File %s/%s has wrong prefix' % (reldir, rfile)
            continue
        if rfile.endswith('.chm'):
            if rfile[:-4] != 'python' + release.replace('.', ''):
                print '    File %s/%s has a different version' % (reldir, rfile)
                continue
        else:
            try:
                prefix, rest = rfile.split('-', 1)
            except:
                prefix, rest = rfile, ''
            if not rest.startswith((release + '-', release + '.')):
                print '    File %s/%s has a different version' % (reldir, rfile)
                continue
        for rx, info in file_descriptions:
            if rx.search(rfile):
                file_desc, os_pk, add_desc = info
                yield rfile, file_desc, os_pk, add_desc
                break
        else:
            print '    File %s/%s not recognized' % (reldir, rfile)
            continue

def query_object(objtype, **params):
    """Find an API object by query parameters."""
    uri = base_url + 'downloads/%s/' % objtype
    uri += '?' + '&'.join('%s=%s' % v for v in params.items())
    resp = requests.get(uri, headers=headers)
    if resp.status_code != 200 or not json.loads(resp.text)['objects']:
        raise RuntimeError('no object for %s params=%r' % (objtype, params))
    obj = json.loads(resp.text)['objects'][0]
    return int(obj['resource_uri'].strip('/').split('/')[-1])

def post_object(objtype, datadict):
    """Create a new API object."""
    resp = requests.post(base_url + 'downloads/' + objtype + '/',
                         data=json.dumps(datadict), headers=headers)
    if resp.status_code != 201:
        try:
            info = json.loads(resp.text)
            print info.get('error_message', 'No error message.')
            print info.get('traceback', '')
        except:
            pass
        print 'Creating %s failed: %s' % (objtype, resp.status_code)
        return -1
    newloc = resp.headers['Location']
    pk = int(newloc.strip('/').split('/')[-1])
    return pk

def main():
    rel = sys.argv[1]
    print 'Querying python.org for release', rel
    rel_pk = query_object('release', name='Python+' + rel)
    print 'Found Release object: id =', rel_pk
    n = 0
    print 'Deleting previous release files'
    resp = requests.delete(base_url + 'downloads/release_file/?release=%s' % rel_pk,
                           headers=headers)
    if resp.status_code != 204:
        raise RuntimeError('deleting previous releases failed: %s' % resp.status_code)
    for rfile, file_desc, os_pk, add_desc in list_files(rel):
        print 'Creating ReleaseFile object for', rfile
        file_dict = build_file_dict(rel, rfile, rel_pk, file_desc, os_pk, add_desc)
        file_pk = post_object('release_file', file_dict)
        if file_pk >= 0:
            print 'Created as id =', file_pk
            n += 1
    print 'Done - %d files added' % n

main()
