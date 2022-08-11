#!/usr/bin/env python
"""
Script to add ReleaseFile objects for Python releases on the new pydotorg.
To use (RELEASE is something like 3.3.5rc2):

* Copy this script to dl-files (it needs access to all the release files).
  You could also download all files, then you need to adapt the "ftp_root"
  string below.

* Make sure all download files are in place in the correct /srv/www.python.org
  subdirectory.

* Create a new Release object via the Django admin (adding via API is
  currently broken), the name MUST be "Python RELEASE".

* Put an AUTH_INFO variable containing "username:api_key" in your environment.

* Call this script as "python add-to-pydotorg.py RELEASE".

  Each call will remove all previous file objects, so you can call the script
  multiple times.

Georg Brandl, March 2014.
"""
from __future__ import print_function

import hashlib
import json
import os
from os import path
import re
import sys
import time

import requests

try:
    auth_info = os.environ['AUTH_INFO']
except KeyError:
    print('Please set an environment variable named AUTH_INFO '
        'containing "username:api_key".')
    sys.exit()

base_url = 'https://www.python.org/api/v1/'
ftp_root = '/srv/www.python.org/ftp/python/'
download_root = 'https://www.python.org/ftp/python/'

tag_cre = re.compile(r'(\d+)(?:\.(\d+)(?:\.(\d+))?)?(?:([ab]|rc)(\d+))?$')

headers = {'Authorization': 'ApiKey %s' % auth_info, 'Content-Type': 'application/json'}

rx = re.compile
# value is (file "name", OS id, file "description")
file_descriptions = [
    (rx(r'\.tgz$'),              ('Gzipped source tarball', 3, '')),
    (rx(r'\.tar\.xz$'),          ('XZ compressed source tarball', 3, '')),
    (rx(r'-webinstall\.exe$'),   ('', 0, '')),  # OS 0 = ignore on the Web
    (rx(r'-embed-amd64\.zip$'),  ('Windows embeddable package (64-bit)', 1, '')),
    (rx(r'-embed-arm64\.zip$'),  ('Windows embeddable package (ARM64)', 1, '')),
    (rx(r'-arm64\.exe$'),        ('Windows installer (ARM64)', 1, 'Experimental')),
    (rx(r'-amd64\.exe$'),        ('Windows installer (64-bit)', 1, 'Recommended')),
    (rx(r'-embed-win32\.zip$'),  ('Windows embeddable package (32-bit)', 1, '')),
    (rx(r'\.exe$'),              ('Windows installer (32-bit)', 1, '')),
    (rx(r'\.chm$'),              ('Windows help file', 1, '')),
    (rx(r'-macosx10\.5(_rev\d)?\.(dm|pk)g$'),  ('macOS 32-bit i386/PPC installer', 2,
                                  'for Mac OS X 10.5 and later')),
    (rx(r'-macosx10\.6(_rev\d)?\.(dm|pk)g$'),  ('macOS 64-bit/32-bit Intel installer', 2,
                                  'for Mac OS X 10.6 and later')),
    (rx(r'-macos(x)?10\.9\.(dm|pk)g$'),  ('macOS 64-bit Intel-only installer', 2,
                                  'for macOS 10.9 and later, deprecated')),
    (rx(r'-macos(x)?1[1-9](\.[0-9]*)?\.pkg$'),  ('macOS 64-bit universal2 installer', 2,
                                  'for macOS 10.9 and later')),
]

def changelog_for(release):
    new_url = 'http://docs.python.org/release/%s/whatsnew/changelog.html' % release
    if requests.head(new_url).status_code != 200:
        return 'http://hg.python.org/cpython/file/v%s/Misc/NEWS' % release

def slug_for(release):
    return base_version(release).replace(".", "") + \
        ('-' +  release[len(base_version(release)):] if release[len(base_version(release)):] else '')

def sigfile_for(release, rfile):
    return download_root + '%s/%s.asc' % (release, rfile)

def md5sum_for(release, rfile):
    return hashlib.md5(open(ftp_root + base_version(release) + '/' + rfile, 'rb').read()).hexdigest()

def filesize_for(release, rfile):
    return path.getsize(ftp_root + base_version(release) + '/' + rfile)

def make_slug(text):
    return re.sub('[^a-zA-Z0-9_-]', '', text.replace(' ', '-'))

def base_version(release):
    m = tag_cre.match(release)
    return ".".join(m.groups()[:3])

def minor_version_tuple(release):
    m = tag_cre.match(release)
    return (int(m.groups()[0]), int(m.groups()[1]))

def build_file_dict(release, rfile, rel_pk, file_desc, os_pk, add_desc):
    """Return a dictionary with all needed fields for a ReleaseFile object."""
    d = dict(
        name = file_desc,
        slug = slug_for(release) + '-' + make_slug(file_desc)[:40],
        os = '/api/v1/downloads/os/%s/' % os_pk,
        release = '/api/v1/downloads/release/%s/' % rel_pk,
        description = add_desc,
        is_source = os_pk == 3,
        url = download_root + '%s/%s' % (base_version(release), rfile),
        md5_sum = md5sum_for(release, rfile),
        filesize = filesize_for(release, rfile),
        download_button=(
            ("tar.xz" in rfile)
            or ("macos11.pkg" in rfile)
            or (
                rfile.endswith((".msi", ".exe"))
                and ("webinstall" not in rfile)
                and (
                    ((minor_version_tuple(release) >= (3, 9)) and ("amd64" in rfile))
                    or ("amd64" not in rfile)
                )
            )
        ),
    )
    # Upload GPG signature
    if os.path.exists(ftp_root + "%s/%s.asc" % (base_version(release), rfile)):
        d["gpg_signature_file"] = sigfile_for(base_version(release), rfile)
    # Upload Sigstore signature
    if os.path.exists(ftp_root + "%s/%s.sig" % (base_version(release), rfile)):
        d["sigstore_signature_file"] = download_root + '%s/%s.sig' % (release, rfile)
    # Upload Sigstore certificate
    if os.path.exists(ftp_root + "%s/%s.crt" % (base_version(release), rfile)):
        d["sigstore_cert_file"] = download_root + '%s/%s.crt' % (release, rfile)

    return d

def list_files(release):
    """List all of the release's download files."""
    reldir = base_version(release)
    for rfile in os.listdir(path.join(ftp_root, reldir)):
        if not path.isfile(path.join(ftp_root, reldir, rfile)):
            continue
        if rfile.endswith('.asc'):
            continue
        for prefix in ('python', 'Python'):
            if rfile.startswith(prefix):
                break
        else:
            print('    File %s/%s has wrong prefix' % (reldir, rfile))
            continue
        if rfile.endswith('.chm'):
            if rfile[:-4] != 'python' + release.replace('.', ''):
                print('    File %s/%s has a different version' % (reldir, rfile))
                continue
        else:
            try:
                prefix, rest = rfile.split('-', 1)
            except:
                prefix, rest = rfile, ''
            if not rest.startswith((release + '-', release + '.')):
                print('    File %s/%s has a different version' % (reldir, rfile))
                continue
        for rx, info in file_descriptions:
            if rx.search(rfile):
                file_desc, os_pk, add_desc = info
                yield rfile, file_desc, os_pk, add_desc
                break
        else:
            print('    File %s/%s not recognized' % (reldir, rfile))
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
            print(info.get('error_message', 'No error message.'))
            print(info.get('traceback', ''))
        except:
            pass
        print('Creating %s failed: %s' % (objtype, resp.status_code))
        return -1
    newloc = resp.headers['Location']
    pk = int(newloc.strip('/').split('/')[-1])
    return pk

def main():
    rel = sys.argv[1]
    print('Querying python.org for release', rel)
    rel_pk = query_object('release', name='Python+' + rel)
    print('Found Release object: id =', rel_pk)
    n = 0
    file_dicts = {}
    for rfile, file_desc, os_pk, add_desc in list_files(rel):
        file_dict = build_file_dict(rel, rfile, rel_pk, file_desc, os_pk, add_desc)
        key = file_dict['slug']
        if not os_pk:
            continue
        print('Creating ReleaseFile object for', rfile, key)
        if key in file_dicts:
            raise RuntimeError('duplicate slug generated: %s' % key)
        file_dicts[key] = file_dict
    print('Deleting previous release files')
    resp = requests.delete(base_url + 'downloads/release_file/?release=%s' % rel_pk,
                           headers=headers)
    if resp.status_code != 204:
        raise RuntimeError('deleting previous releases failed: %s' % resp.status_code)
    for file_dict in file_dicts.values():
        file_pk = post_object('release_file', file_dict)
        if file_pk >= 0:
            print('Created as id =', file_pk)
            n += 1
    print('Done - %d files added' % n)

if not sys.flags.interactive:
    main()
