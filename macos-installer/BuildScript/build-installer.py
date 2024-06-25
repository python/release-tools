"""
This script is used to build "official" universal installers on macOS.

Usage: see USAGE variable in the script.
"""

import platform, os, sys, getopt, textwrap, shutil, stat, time, pwd, grp
try:
    import urllib2 as urllib_request
except ImportError:
    import urllib.request as urllib_request

STAT_0o755 = ( stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
             | stat.S_IRGRP |                stat.S_IXGRP
             | stat.S_IROTH |                stat.S_IXOTH )

STAT_0o775 = ( stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
             | stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
             | stat.S_IROTH |                stat.S_IXOTH )

VERBOSE = 1

from plistlib import dump
def writePlist(path, plist):
    with open(plist, 'wb') as fp:
        dump(path, fp)

def shellQuote(value):
    """
    Return the string value in a form that can safely be inserted into
    a shell command.
    """
    return "'%s'"%(value.replace("'", "'\"'\"'"))

def grepValue(fn, variable):
    """
    Return the unquoted value of a variable from a file..
    QUOTED_VALUE='quotes'    -> str('quotes')
    UNQUOTED_VALUE=noquotes  -> str('noquotes')
    """
    variable = variable + '='
    for ln in open(fn, 'r'):
        if ln.startswith(variable):
            value = ln[len(variable):].strip()
            return value.strip("\"'")
    raise RuntimeError("Cannot find variable %s" % variable[:-1])

_cache_getVersion = None

def getVersion():
    global _cache_getVersion
    if _cache_getVersion is None:
        _cache_getVersion = grepValue(
            os.path.join(SRCDIR, 'configure'), 'PACKAGE_VERSION')
    return _cache_getVersion

def getVersionMajorMinor():
    return tuple([int(n) for n in getVersion().split('.', 2)])

_cache_getFullVersion = None

def getFullVersion():
    global _cache_getFullVersion
    if _cache_getFullVersion is not None:
        return _cache_getFullVersion
    fn = os.path.join(SRCDIR, 'Include', 'patchlevel.h')
    for ln in open(fn):
        if 'PY_VERSION' in ln:
            _cache_getFullVersion = ln.split()[-1][1:-1]
            return _cache_getFullVersion
    raise RuntimeError("Cannot find full version??")

FW_PREFIX = ["Library", "Frameworks", "Python.framework"]
FW_VERSION_PREFIX = "--undefined--" # initialized in parseOptions
FW_SSL_DIRECTORY = "--undefined--" # initialized in parseOptions
FW_PVT_FRAMEWORKS_DIRECTORY = "--undefined--" # initialized in parseOptions

# The directory we'll use to create the build (will be erased and recreated)
WORKDIR = "**undefined**"

# The directory we'll use to store third-party sources. Set this to something
# else if you don't want to re-fetch required libraries every time.
DEPSRC = os.path.join(WORKDIR, 'third-party')

# The directory we'll use as the root for binary installations.
ROOTDIR = os.path.join(WORKDIR, '_root')

universal_opts_map = { 'universal2': ('arm64', 'x86_64'),
                       '32-bit': ('i386', 'ppc',),
                       '64-bit': ('x86_64', 'ppc64',),
                       'intel':  ('i386', 'x86_64'),
                       'intel-32':  ('i386',),
                       'intel-64':  ('x86_64',),
                       '3-way':  ('ppc', 'i386', 'x86_64'),
                       'all':    ('i386', 'ppc', 'x86_64', 'ppc64',) }
default_target_map = {
        'universal2': '10.9',
}

UNIVERSALOPTS = tuple(universal_opts_map.keys())

UNIVERSALARCHS = 'intel-64'

ARCHLIST = universal_opts_map[UNIVERSALARCHS]

# Source directory (assume we're in Mac/BuildScript)
SRCDIR = os.path.dirname(
        os.path.dirname(
            os.path.dirname(
                os.path.abspath(__file__
        ))))

# $MACOSX_DEPLOYMENT_TARGET -> minimum OS X level
DEPTARGET = '10.9'

def getDeptargetTuple():
    return tuple([int(n) for n in DEPTARGET.split('.')[0:2]])

def getBuildTuple():
    return tuple([int(n) for n in platform.mac_ver()[0].split('.')[0:2]])

def getTargetCompilers():
    target_cc_map = {
        '10.6': ('gcc', 'g++'),
        '10.7': ('gcc', 'g++'),
        '10.8': ('gcc', 'g++'),
    }
    return target_cc_map.get(DEPTARGET, ('clang', 'clang++') )

CC, CXX = getTargetCompilers()

USAGE = textwrap.dedent("""\
    Usage: build_python [options]

    Options:
    -? or -h:            Show this message
    -b DIR
    --build-dir=DIR:     Create build here (default: %(WORKDIR)r)
    --third-party=DIR:   Store third-party sources here (default: %(DEPSRC)r)
    --src-dir=DIR:       Location of the Python sources (default: %(SRCDIR)r)
    --dep-target=10.n    macOS deployment target (default: %(DEPTARGET)r)
    --universal-archs=x  universal architectures (options: %(UNIVERSALOPTS)r, default: %(UNIVERSALARCHS)r)
""")% globals()

# List of names of third party software built with this installer.
# The names will be inserted into the rtf version of the License.
THIRD_PARTY_LIBS = []

# Instructions for building libraries that are necessary for building a
# batteries included python.
#   [The recipes are defined here for convenience but instantiated later after
#    command line options have been processed.]
def library_recipes():
    result = []

    result.extend([
          dict(
              name="OpenSSL 3.0.13",
              url="https://www.openssl.org/source/openssl-3.0.13.tar.gz",
              checksum='88525753f79d3bec27d2fa7c66aa0b92b3aa9498dafd93d7cfa4b3780cdae313',
              buildrecipe=build_universal_openssl,
              configure=None,
              install=None,
          ),
    ])

    tcl_tk_ver='8.6.14'
    base_url = "https://prdownloads.sourceforge.net/tcl/{what}{version}-src.tar.gz"
    result.extend([
          dict(
              name=f"Tcl {tcl_tk_ver}",
              url=base_url.format(what="tcl", version=tcl_tk_ver),
              checksum='5880225babf7954c58d4fb0f5cf6279104ce1cd6aa9b71e9a6322540e1c4de66',
              configure=None,
              useLDFlags=False,
              install=f'make -C macosx install-embedded SUBFRAMEWORK=1 ' \
                      f'DESTDIR={shellQuote(os.path.join(WORKDIR, "libraries"))} ' \
                      f'DYLIB_INSTALL_DIR={os.path.join("/", *FW_PVT_FRAMEWORKS_DIRECTORY, "Tcl.framework")} ' \
                      f'CFLAGS="-mmacosx-version-min={DEPTARGET} -arch {" -arch ".join(ARCHLIST)}" ',
              ),

          dict(
              name=f"Tk {tcl_tk_ver}",
              url=base_url.format(what="tk", version=tcl_tk_ver),
              checksum='8ffdb720f47a6ca6107eac2dd877e30b0ef7fac14f3a84ebbd0b3612cee41a94',
              configure=None,
              useLDFlags=False,
              install=f'make -C macosx install-embedded SUBFRAMEWORK=1 ' \
                      f'DESTDIR={shellQuote(os.path.join(WORKDIR, "libraries"))} ' \
                      f'DYLIB_INSTALL_DIR={os.path.join("/", *FW_PVT_FRAMEWORKS_DIRECTORY, "Tk.framework")} ' \
                      f'CFLAGS="-mmacosx-version-min={DEPTARGET} -arch {" -arch ".join(ARCHLIST)}" ',
              ),
    ])

    result.extend([
          dict(
              name="XZ 5.2.3",
              url="http://tukaani.org/xz/xz-5.2.3.tar.gz",
              checksum='71928b357d0a09a12a4b4c5fafca8c31c19b0e7d3b8ebb19622e96f26dbf28cb',
              configure_pre=[
                    '--disable-dependency-tracking',
                  ]
              ),
    ])

    result.extend([
          dict(
              name="NCurses 5.9",
              url="http://ftp.gnu.org/pub/gnu/ncurses/ncurses-5.9.tar.gz",
              checksum='9046298fb440324c9d4135ecea7879ffed8546dd1b58e59430ea07a4633f563b',
              configure_pre=[
                  "--enable-pc-files",
                  "--enable-widec",
                  "--without-cxx",
                  "--without-cxx-binding",
                  "--without-ada",
                  "--without-curses-h",
                  "--enable-shared",
                  "--with-shared",
                  "--without-debug",
                  "--without-normal",
                  "--without-tests",
                  "--without-manpages",
                  "--datadir=/usr/share",
                  "--sysconfdir=/etc",
                  "--sharedstatedir=/usr/com",
                  "--with-terminfo-dirs=/usr/share/terminfo",
                  "--with-default-terminfo-dir=/usr/share/terminfo",
                  "--libdir=/Library/Frameworks/Python.framework/Versions/%s/lib"%(getVersion(),),
              ],
              patchscripts=[
                  ("ftp://ftp.invisible-island.net/ncurses//5.9/ncurses-5.9-20120616-patch.sh.bz2",
                   "f54bf02a349f96a7c4f0d00922f3a0d4"),
                   ],
              useLDFlags=False,
              install='make && make install DESTDIR=%s && cd %s/usr/local/lib && ln -fs ../../../Library/Frameworks/Python.framework/Versions/%s/lib/lib* .'%(
                  shellQuote(os.path.join(WORKDIR, 'libraries')),
                  shellQuote(os.path.join(WORKDIR, 'libraries')),
                  getVersion(),
                  ),
          ),

          dict(
              name="SQLite 3.45.3",
              url="https://sqlite.org/2024/sqlite-autoconf-3450300.tar.gz",
              checksum="b2809ca53124c19c60f42bf627736eae011afdcc205bb48270a5ee9a38191531",
              extra_cflags=('-Os '
                            '-DSQLITE_ENABLE_FTS5 '
                            '-DSQLITE_ENABLE_FTS4 '
                            '-DSQLITE_ENABLE_FTS3_PARENTHESIS '
                            '-DSQLITE_ENABLE_RTREE '
                            '-DSQLITE_OMIT_AUTOINIT '
                            '-DSQLITE_TCL=0 '
                            ),
              configure_pre=[
                  '--enable-threadsafe',
                  '--enable-shared=no',
                  '--enable-static=yes',
                  '--disable-readline',
                  '--disable-dependency-tracking',
              ]
          ),

          dict(
              name="libmpdec 4.0.0",
              url="https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-4.0.0.tar.gz",
              checksum="942445c3245b22730fd41a67a7c5c231d11cb1b9936b9c0f76334fb7d0b4468c",
              configure_pre=[
                  "--disable-cxx",
                  "MACHINE=universal",
              ]
          ),
        ])

    return result

# Instructions for building packages inside the .mpkg.
def pkg_recipes():
    result = [
        dict(
            name="PythonFramework",
            long_name="Python Framework",
            source="/Library/Frameworks/Python.framework",
            readme="""\
                This package installs Python.framework, that is the python
                interpreter and the standard library.
            """,
            postflight="scripts/postflight.framework",
            selected='selected',
        ),
        dict(
            name="PythonApplications",
            long_name="GUI Applications",
            source="/Applications/Python %(VER)s",
            readme="""\
                This package installs IDLE (an interactive Python IDE),
                Python Launcher and Build Applet (create application bundles
                from python scripts).

                It also installs a number of examples and demos.
                """,
            required=False,
            selected='selected',
        ),
        dict(
            name="PythonUnixTools",
            long_name="UNIX command-line tools",
            source="/usr/local/bin",
            readme="""\
                This package installs the unix tools in /usr/local/bin for
                compatibility with older releases of Python. This package
                is not necessary to use Python.
                """,
            required=False,
            selected='selected',
        ),
        dict(
            name="PythonDocumentation",
            long_name="Python Documentation",
            topdir="/Library/Frameworks/Python.framework/Versions/%(VER)s/Resources/English.lproj/Documentation",
            source="/pydocs",
            readme="""\
                This package installs the python documentation at a location
                that is usable for pydoc and IDLE.
                """,
            postflight="scripts/postflight.documentation",
            required=False,
            selected='selected',
        ),
        dict(
            name="PythonProfileChanges",
            long_name="Shell profile updater",
            readme="""\
                This packages updates your shell profile to make sure that
                the Python tools are found by your shell in preference of
                the system provided Python tools.

                If you don't install this package you'll have to add
                "/Library/Frameworks/Python.framework/Versions/%(VER)s/bin"
                to your PATH by hand.
                """,
            postflight="scripts/postflight.patch-profile",
            topdir="/Library/Frameworks/Python.framework",
            source="/empty-dir",
            required=False,
            selected='selected',
        ),
        dict(
            name="PythonInstallPip",
            long_name="Install or upgrade pip",
            readme="""\
                This package installs (or upgrades from an earlier version)
                pip, a tool for installing and managing Python packages.
                """,
            postflight="scripts/postflight.ensurepip",
            topdir="/Library/Frameworks/Python.framework",
            source="/empty-dir",
            required=False,
            selected='selected',
        ),
        dict(
            name="PythonTFramework",
            long_name="Experimental free-threaded Python Framework",
            source="/Library/Frameworks/PythonT.framework",
            readme="""\
                This optional package installs a free-threaded PythonT.framework,
                including a separate python interpeter capable of running without
                the global interpreter lock. It is installed independently of
                the normal interpeter and includes its own standard library.
                See the release notice for more information.
            """,
            postflight="scripts/postflight.frameworkt",
            required=False,
            selected='unselected',
        ),
    ]

    return result

def fatal(msg):
    """
    A fatal error, bail out.
    """
    sys.stderr.write('FATAL: ')
    sys.stderr.write(msg)
    sys.stderr.write('\n')
    sys.exit(1)

def fileContents(fn):
    """
    Return the contents of the named file
    """
    return open(fn, 'r').read()

def runCommand(commandline):
    """
    Run a command and raise RuntimeError if it fails. Output is suppressed
    unless the command fails.
    """
    if VERBOSE:
        print(f"%%% runCommand: [{commandline}]")
    fd = os.popen(commandline, 'r')
    data = fd.read()
    xit = fd.close()
    if xit is not None:
        sys.stdout.write(data)
        raise RuntimeError("command failed: %s"%(commandline,))

    if VERBOSE:
        sys.stdout.write(data); sys.stdout.flush()

def captureCommand(commandline):
    if VERBOSE:
        print(f"%%% captureCommand: [{commandline}]")
    fd = os.popen(commandline, 'r')
    data = fd.read()
    xit = fd.close()
    if xit is not None:
        sys.stdout.write(data)
        raise RuntimeError("command failed: %s"%(commandline,))

    return data

def checkEnvironment():
    """
    Check that we're running on a supported system.
    """

    if sys.version_info[0:2] < (3, 9):
        fatal("This script must be run with Python 3.9 (or later)")

    if platform.system() != 'Darwin':
        fatal("This script should be run on a macOS 11 (or later) system")

    if int(platform.release().split('.')[0]) < 20:
        fatal("This script should be run on a macOS 11 (or later) system")

    print("")

    # Remove inherited environment variables which might influence build
    environ_var_prefixes = ['CPATH', 'C_INCLUDE_', 'DYLD_', 'LANG', 'LC_',
                            'LD_', 'LIBRARY_', 'PATH', 'PKG_CONFIG', 'PYTHON']
    for ev in list(os.environ):
        for prefix in environ_var_prefixes:
            if ev.startswith(prefix) :
                print("INFO: deleting environment variable %s=%s" % (
                                                    ev, os.environ[ev]))
                del os.environ[ev]

    base_path = '/bin:/sbin:/usr/bin:/usr/sbin'
    if 'SDK_TOOLS_BIN' in os.environ:
        base_path = os.environ['SDK_TOOLS_BIN'] + ':' + base_path
    os.environ['PATH'] = base_path
    print("Setting default PATH: %s"%(os.environ['PATH']))

def parseOptions(args=None):
    """
    Parse arguments and update global settings.
    """
    global WORKDIR, DEPSRC, ROOTDIR, SRCDIR, DEPTARGET
    global UNIVERSALOPTS, UNIVERSALARCHS, ARCHLIST, CC, CXX
    global FW_VERSION_PREFIX
    global FW_SSL_DIRECTORY
    global FW_PVT_FRAMEWORKS_DIRECTORY

    if args is None:
        args = sys.argv[1:]

    try:
        options, args = getopt.getopt(args, '?hb',
                [ 'build-dir=', 'third-party=', 'sdk-path=' , 'src-dir=',
                  'dep-target=', 'universal-archs=', 'help' ])
    except getopt.GetoptError:
        print(sys.exc_info()[1])
        sys.exit(1)

    if args:
        print("Additional arguments")
        sys.exit(1)

    deptarget = None
    for k, v in options:
        if k in ('-h', '-?', '--help'):
            print(USAGE)
            sys.exit(0)

        elif k in ('-d', '--build-dir'):
            WORKDIR=v

        elif k in ('--third-party',):
            DEPSRC=v

        elif k in ('--sdk-path',):
            print(" WARNING: --sdk-path is no longer supported")

        elif k in ('--src-dir',):
            SRCDIR=v

        elif k in ('--dep-target', ):
            DEPTARGET=v
            deptarget=v

        elif k in ('--universal-archs', ):
            if v in UNIVERSALOPTS:
                UNIVERSALARCHS = v
                ARCHLIST = universal_opts_map[UNIVERSALARCHS]
                if deptarget is None:
                    # Select alternate default deployment
                    # target
                    DEPTARGET = default_target_map.get(v, '10.9')
            else:
                raise NotImplementedError(v)

        else:
            raise NotImplementedError(k)

    SRCDIR=os.path.abspath(SRCDIR)
    WORKDIR=os.path.abspath(WORKDIR)
    ROOTDIR = os.path.join(WORKDIR, '_root')
    DEPSRC = os.path.abspath(DEPSRC)

    CC, CXX = getTargetCompilers()

    FW_VERSION_PREFIX = FW_PREFIX[:] + ["Versions", getVersion()]
    FW_SSL_DIRECTORY = FW_VERSION_PREFIX[:] + ["etc", "openssl"]
    FW_PVT_FRAMEWORKS_DIRECTORY = FW_VERSION_PREFIX[:] + ["Frameworks"]

    print("-- Settings:")
    print("   * Source directory:    %s" % SRCDIR)
    print("   * Build directory:     %s" % WORKDIR)
    print("   * Third-party source:  %s" % DEPSRC)
    print("   * Deployment target:   %s" % DEPTARGET)
    print("   * Universal archs:     %s" % str(ARCHLIST))
    print("   * C compiler:          %s" % CC)
    print("   * C++ compiler:        %s" % CXX)
    print("")
    print(" -- Building a Python %s framework at patch level %s"
                % (getVersion(), getFullVersion()))
    print("")

def extractArchive(builddir, archiveName):
    """
    Extract a source archive into 'builddir'. Returns the path of the
    extracted archive.

    XXX: This function assumes that archives contain a toplevel directory
    that is has the same name as the basename of the archive. This is
    safe enough for almost anything we use.  Unfortunately, it does not
    work for current Tcl and Tk source releases where the basename of
    the archive ends with "-src" but the uncompressed directory does not.
    For now, just special case Tcl and Tk tar.gz downloads.
    """
    curdir = os.getcwd()
    try:
        os.chdir(builddir)
        if archiveName.endswith('.tar.gz'):
            retval = os.path.basename(archiveName[:-7])
            if ((retval.startswith('tcl') or retval.startswith('tk'))
                    and retval.endswith('-src')):
                retval = retval[:-4]
                # Strip rcxx suffix from Tcl/Tk release candidates
                retval_rc = retval.find('rc')
                if retval_rc > 0:
                    retval = retval[:retval_rc]
            if os.path.exists(retval):
                shutil.rmtree(retval)
            commandline = f"tar zxf {shellQuote(archiveName)} 2>&1"

        elif archiveName.endswith('.tar.bz2'):
            retval = os.path.basename(archiveName[:-8])
            if os.path.exists(retval):
                shutil.rmtree(retval)
            commandline = f"tar jxf {shellQuote(archiveName)} 2>&1"

        elif archiveName.endswith('.tar'):
            retval = os.path.basename(archiveName[:-4])
            if os.path.exists(retval):
                shutil.rmtree(retval)
            commandline = f"tar xf {shellQuote(archiveName)} 2>&1"

        elif archiveName.endswith('.zip'):
            retval = os.path.basename(archiveName[:-4])
            if os.path.exists(retval):
                shutil.rmtree(retval)
            commandline = f"unzip {shellQuote(archiveName)} 2>&1"

        if VERBOSE:
            print(f"%%% extractArchive: [{commandline}]")
        fp = os.popen(commandline, 'r')
        data = fp.read()
        xit = fp.close()
        if xit is not None:
            sys.stdout.write(data)
            raise RuntimeError("Cannot extract %s"%(archiveName,))

        return os.path.join(builddir, retval)

    finally:
        os.chdir(curdir)

def downloadURL(url, fname):
    """
    Download the contents of the url into the file.
    """
    fpIn = urllib_request.urlopen(url)
    fpOut = open(fname, 'wb')
    block = fpIn.read(10240)
    try:
        while block:
            fpOut.write(block)
            block = fpIn.read(10240)
        fpIn.close()
        fpOut.close()
    except:
        try:
            os.unlink(fname)
        except OSError:
            pass

def verifyThirdPartyFile(url, checksum, fname):
    """
    Download file from url to filename fname if it does not already exist.
    Abort if file contents does not match supplied checksum.
    """
    name = os.path.basename(fname)
    if os.path.exists(fname):
        print("Using local copy of %s"%(name,))
    else:
        print("Did not find local copy of %s"%(name,))
        print("Downloading %s"%(name,))
        downloadURL(url, fname)
        print("Archive for %s stored as %s"%(name, fname))
    if len(checksum) == 32:
        algo = 'md5'
    elif len(checksum) == 64:
        algo = 'sha256'
    else:
        raise ValueError(checksum)
    if os.system(
            'CHECKSUM=$(openssl %s %s) ; test "${CHECKSUM##*= }" = "%s"'
                % (algo, shellQuote(fname), checksum) ):
        fatal('%s checksum mismatch for file %s' % (algo, fname))

def build_universal_openssl(basedir, archList):
    """
    Special case build recipe for universal build of openssl.

    The upstream OpenSSL build system does not directly support
    OS X universal builds.  We need to build each architecture
    separately then lipo them together into fat libraries.
    """

    def build_openssl_arch(archbase, arch):
        "Build one architecture of openssl"
        arch_opts = {
            "i386": ["darwin-i386-cc"],
            "x86_64": ["darwin64-x86_64-cc", "enable-ec_nistp_64_gcc_128"],
            "arm64": ["darwin64-arm64-cc"],
            "ppc": ["darwin-ppc-cc"],
            "ppc64": ["darwin64-ppc-cc"],
        }

        # Somewhere between OpenSSL 1.1.0j and 1.1.1c, changes cause the
        # "enable-ec_nistp_64_gcc_128" option to get compile errors when
        # building on our 10.6 gcc-4.2 environment.  There have been other
        # reports of projects running into this when using older compilers.
        # So, for now, do not try to use "enable-ec_nistp_64_gcc_128" when
        # building for 10.6.
        if getDeptargetTuple() == (10, 6):
            arch_opts['x86_64'].remove('enable-ec_nistp_64_gcc_128')

        configure_opts = [
            "no-idea",
            "no-mdc2",
            "no-rc5",
            "no-zlib",
            "no-ssl3",
            # "enable-unit-test",
            "shared",
            "--prefix=%s"%os.path.join("/", *FW_VERSION_PREFIX),
            "--openssldir=%s"%os.path.join("/", *FW_SSL_DIRECTORY),
        ]
        runCommand(" ".join(["perl", "Configure"]
                        + arch_opts[arch] + configure_opts))
        runCommand("make depend")
        runCommand("make all")
        runCommand("make install_sw DESTDIR=%s"%shellQuote(archbase))
        # runCommand("make test")
        return

    srcdir = os.getcwd()
    universalbase = os.path.join(srcdir, "..",
                        os.path.basename(srcdir) + "-universal")
    os.mkdir(universalbase)
    archbasefws = []
    for arch in archList:
        # fresh copy of the source tree
        archsrc = os.path.join(universalbase, arch, "src")
        shutil.copytree(srcdir, archsrc, symlinks=True)
        # install base for this arch
        archbase = os.path.join(universalbase, arch, "root")
        os.mkdir(archbase)
        # Python framework base within install_prefix:
        # the build will install into this framework..
        # This is to ensure that the resulting shared libs have
        # the desired real install paths built into them.
        archbasefw = os.path.join(archbase, *FW_VERSION_PREFIX)

        # build one architecture
        os.chdir(archsrc)
        build_openssl_arch(archbase, arch)
        os.chdir(srcdir)
        archbasefws.append(archbasefw)

    # copy arch-independent files from last build into the basedir framework
    basefw = os.path.join(basedir, *FW_VERSION_PREFIX)
    shutil.copytree(
            os.path.join(archbasefw, "include", "openssl"),
            os.path.join(basefw, "include", "openssl")
            )

    shlib_version_number = grepValue(os.path.join(archsrc, "Makefile"),
            "SHLIB_VERSION_NUMBER")
    #   e.g. -> "1.0.0"
    libcrypto = "libcrypto.dylib"
    libcrypto_versioned = libcrypto.replace(".", "."+shlib_version_number+".")
    #   e.g. -> "libcrypto.1.0.0.dylib"
    libssl = "libssl.dylib"
    libssl_versioned = libssl.replace(".", "."+shlib_version_number+".")
    #   e.g. -> "libssl.1.0.0.dylib"

    try:
        os.mkdir(os.path.join(basefw, "lib"))
    except OSError:
        pass

    # merge the individual arch-dependent shared libs into a fat shared lib
    archbasefws.insert(0, basefw)
    for (lib_unversioned, lib_versioned) in [
                (libcrypto, libcrypto_versioned),
                (libssl, libssl_versioned)
            ]:
        runCommand("lipo -create -output " +
                    " ".join(shellQuote(
                            os.path.join(fw, "lib", lib_versioned))
                                    for fw in archbasefws))
        # and create an unversioned symlink of it
        os.symlink(lib_versioned, os.path.join(basefw, "lib", lib_unversioned))

    # Create links in the temp include and lib dirs that will be injected
    # into the Python build so that setup.py can find them while building
    # and the versioned links so that the setup.py post-build import test
    # does not fail.
    relative_path = os.path.join("..", "..", "..", *FW_VERSION_PREFIX)
    for fn in [
            ["include", "openssl"],
            ["lib", libcrypto],
            ["lib", libssl],
            ["lib", libcrypto_versioned],
            ["lib", libssl_versioned],
        ]:
        os.symlink(
            os.path.join(relative_path, *fn),
            os.path.join(basedir, "usr", "local", *fn)
        )

    return

def buildRecipe(recipe, basedir, archList):
    """
    Build software using a recipe. This function does the
    'configure;make;make install' dance for C software, with a possibility
    to customize this process, basically a poor-mans DarwinPorts.
    """
    curdir = os.getcwd()

    name = recipe['name']
    THIRD_PARTY_LIBS.append(name)
    url = recipe['url']
    configure = recipe.get('configure', './configure')
    buildrecipe = recipe.get('buildrecipe', None)
    install = recipe.get('install', 'make && make install DESTDIR=%s'%(
        shellQuote(basedir)))

    archiveName = os.path.split(url)[-1]
    sourceArchive = os.path.join(DEPSRC, archiveName)

    if not os.path.exists(DEPSRC):
        os.mkdir(DEPSRC)

    verifyThirdPartyFile(url, recipe['checksum'], sourceArchive)
    print("Extracting archive for %s"%(name,))
    buildDir=os.path.join(WORKDIR, '_bld')
    if not os.path.exists(buildDir):
        os.mkdir(buildDir)

    workDir = extractArchive(buildDir, sourceArchive)
    os.chdir(workDir)

    for patch in recipe.get('patches', ()):
        if isinstance(patch, tuple):
            url, checksum = patch
            fn = os.path.join(DEPSRC, os.path.basename(url))
            verifyThirdPartyFile(url, checksum, fn)
        else:
            # patch is a file in the source directory
            fn = os.path.join(curdir, patch)
        runCommand('patch -p%s < %s'%(recipe.get('patchlevel', 1),
            shellQuote(fn),))

    for patchscript in recipe.get('patchscripts', ()):
        if isinstance(patchscript, tuple):
            url, checksum = patchscript
            fn = os.path.join(DEPSRC, os.path.basename(url))
            verifyThirdPartyFile(url, checksum, fn)
        else:
            # patch is a file in the source directory
            fn = os.path.join(curdir, patchscript)
        if fn.endswith('.bz2'):
            runCommand('bunzip2 -fk %s' % shellQuote(fn))
            fn = fn[:-4]
        runCommand('sh %s' % shellQuote(fn))
        os.unlink(fn)

    if 'buildDir' in recipe:
        os.chdir(recipe['buildDir'])

    if configure is not None:
        configure_args = [
            "--prefix=/usr/local",
            "--enable-static",
            "--disable-shared",
        ]

        if 'configure_pre' in recipe:
            args = list(recipe['configure_pre'])
            if '--disable-static' in args:
                configure_args.remove('--enable-static')
            if '--enable-shared' in args:
                configure_args.remove('--disable-shared')
            configure_args.extend(args)

        if recipe.get('useLDFlags', 1):
            configure_args.extend([
                "CFLAGS=%s-mmacosx-version-min=%s -arch %s "
                            "-I%s/usr/local/include"%(
                        recipe.get('extra_cflags', ''),
                        DEPTARGET,
                        ' -arch '.join(archList),
                        shellQuote(basedir)[1:-1],),
                "LDFLAGS=-mmacosx-version-min=%s -L%s/usr/local/lib -arch %s"%(
                    DEPTARGET,
                    shellQuote(basedir)[1:-1],
                    ' -arch '.join(archList)),
            ])
        else:
            configure_args.extend([
                "CFLAGS=%s-mmacosx-version-min=%s -arch %s "
                            "-I%s/usr/local/include"%(
                        recipe.get('extra_cflags', ''),
                        DEPTARGET,
                        ' -arch '.join(archList),
                        shellQuote(basedir)[1:-1],),
            ])

        if 'configure_post' in recipe:
            configure_args = configure_args + list(recipe['configure_post'])

        configure_args.insert(0, configure)
        configure_args = [ shellQuote(a) for a in configure_args ]

        print("Running configure for %s"%(name,))
        runCommand(' '.join(configure_args) + ' 2>&1')

    if buildrecipe is not None:
        # call special-case build recipe, e.g. for openssl
        buildrecipe(basedir, archList)

    if install is not None:
        print("Running install for %s"%(name,))
        runCommand('{ ' + install + ' ;} 2>&1')

    print("Done %s"%(name,))
    print("")

    os.chdir(curdir)

def buildLibraries():
    """
    Build our dependencies into $WORKDIR/libraries/usr/local
    """
    print("")
    print("Building required libraries")
    print("")
    universal = os.path.join(WORKDIR, 'libraries')
    os.mkdir(universal)
    os.makedirs(os.path.join(universal, 'usr', 'local', 'lib'))
    os.makedirs(os.path.join(universal, 'usr', 'local', 'include'))

    for recipe in library_recipes():
        buildRecipe(recipe, universal, ARCHLIST)


def buildPythonDocs():
    # This stores the documentation as Resources/English.lproj/Documentation
    # inside the framework. pydoc and IDLE will pick it up there.
    print("Install python documentation")
    buildDir = os.path.join('../../Doc')
    docdir = os.path.join(ROOTDIR, 'pydocs')
    curDir = os.getcwd()
    os.chdir(buildDir)
    runCommand('make clean')

    # Search third-party source directory for a pre-built version of the docs.
    #   Use the naming convention of the docs.python.org html downloads:
    #       python-3.9.0b1-docs-html.tar.bz2
    doctarfiles = [ f for f in os.listdir(DEPSRC)
        if f.startswith('python-'+getFullVersion())
        if f.endswith('-docs-html.tar.bz2') ]
    if doctarfiles:
        doctarfile = doctarfiles[0]
        if not os.path.exists('build'):
            os.mkdir('build')
        # if build directory existed, it was emptied by make clean, above
        os.chdir('build')
        # Extract the first archive found for this version into build
        runCommand('tar xjf %s'%shellQuote(os.path.join(DEPSRC, doctarfile)))
        # see if tar extracted a directory ending in -docs-html
        archivefiles = [ f for f in os.listdir('.')
            if f.endswith('-docs-html')
            if os.path.isdir(f) ]
        if archivefiles:
            archivefile = archivefiles[0]
            # make it our 'Docs/build/html' directory
            print(' -- using pre-built python documentation from %s'%archivefile)
            os.rename(archivefile, 'html')
        os.chdir(buildDir)

    htmlDir = os.path.join('build', 'html')
    if not os.path.exists(htmlDir):
        # Create virtual environment for docs builds with blurb and sphinx
        runCommand('make venv')
        runCommand('make html PYTHON=venv/bin/python')
    os.rename(htmlDir, docdir)
    os.chdir(curDir)


def buildPythonFramework(python_framework_name, buildDir, configure_options):
    print(f"Building {python_framework_name} framework for {UNIVERSALARCHS} architectures in {buildDir}")
    if configure_options:
        print(f"    with additional configure options: {configure_options}")

    # Steps that are only performed when building the main Python.framework
    #   which must be the first framework built
    main_framework = python_framework_name == 'Python'

    curdir = os.getcwd()
    os.chdir(buildDir)

    workDirUsrLocal = os.path.join(WORKDIR, 'libraries', 'usr', 'local')
    workDirUsrLocalInclude = os.path.join(workDirUsrLocal, 'include')
    workDirUsrLocalLib = os.path.join(workDirUsrLocal, 'lib')
    workDirFrameworks = os.path.join(WORKDIR, 'libraries', 'Frameworks')

    if main_framework:
        # Create links from the private Tcl and Tk frameworks to the
        # build usr/local/include and usr/local/lib directories as it is
        # easier than trying to get -framework compiler/linker options to
        # work for them.
        runCommand(f'ln -s {os.path.join(workDirFrameworks, "Tcl.framework", "Headers", "*")} {workDirUsrLocalInclude}')
        runCommand(f'ln -s {os.path.join(workDirFrameworks, "Tk.framework", "Headers", "*")} {workDirUsrLocalInclude}')
        runCommand(f'ln -s {os.path.join(workDirFrameworks, "Tcl.framework", "Tcl")} {os.path.join(workDirUsrLocalLib, "libtcl.dylib")}')
        runCommand(f'ln -s {os.path.join(workDirFrameworks, "Tk.framework", "Tk")} {os.path.join(workDirUsrLocalLib, "libtk.dylib")}')
        # Remove Tk Wish.app if built
        runCommand(f'rm -rf {os.path.join(workDirFrameworks, "Tk.framework", "Resources", "Wish.app")}')

    # Create links in the build directory to private frameworks (e.g. Tcl, Tk).
    # The interpreter will find them during the import check because of the
    # DYLD_FRAMEWORK_PATH for the build directory created by configure/Makefile.
    runCommand(f'ln -s {os.path.join(workDirFrameworks, "*")} .')

    runCommand(f"{shellQuote(os.path.join(SRCDIR, 'configure'))} "
               f"--enable-framework "
               f"--with-framework-name={python_framework_name} "
               f"--enable-universalsdk=/ "
               f"--with-universal-archs={UNIVERSALARCHS} "
               f"{configure_options} "
               f"--enable-optimizations "
               f"--with-lto "
               f"--without-ensurepip "
               f"--with-system-libmpdec "
               f"--with-openssl='{workDirUsrLocal}' "
               f"TCLTK_CFLAGS='-I{workDirUsrLocalInclude}' "
               f"TCLTK_LIBS='-L{workDirUsrLocalLib} -ltcl -ltk' "
               f"CFLAGS='-g -I{workDirUsrLocalInclude}' "
               f"LDFLAGS='-g -L{workDirUsrLocalLib}' "
               f"2>&1")

    runshared_for_make = f" RUNSHARED='{grepValue("Makefile", "RUNSHARED")} DYLD_LIBRARY_PATH={workDirUsrLocalLib}'"

    # Look for environment value BUILDINSTALLER_BUILDPYTHON_MAKE_EXTRAS
    # and, if defined, append its value to the make command.  This allows
    # us to pass in version control tags, like GITTAG, to a build from a
    # tarball rather than from a vcs checkout, thus eliminating the need
    # to have a working copy of the vcs program on the build machine.
    #
    # A typical use might be:
    #      export BUILDINSTALLER_BUILDPYTHON_MAKE_EXTRAS=" \
    #                         GITVERSION='echo 123456789a' \
    #                         GITTAG='echo v3.6.0' \
    #                         GITBRANCH='echo 3.6'"

    make_extras = os.getenv("BUILDINSTALLER_BUILDPYTHON_MAKE_EXTRAS", default="")
    runCommand("make " + make_extras + runshared_for_make)

    runCommand(f"make install DESTDIR={shellQuote(ROOTDIR)} {runshared_for_make}")

    if main_framework:
        # installs the Tools into the applications directory
        runCommand(f"make frameworkinstallextras DESTDIR={shellQuote(ROOTDIR)} {runshared_for_make}")

        # Copy the third-party shared libraries and frameworks into the main fw.
        #   Other python fw variants link to these as well.
        print("Copying required shared libraries")
        if os.path.exists(os.path.join(WORKDIR, 'libraries', 'Library')):
            build_lib_dir = os.path.join(
                    WORKDIR, 'libraries', 'Library', 'Frameworks',
                    'Python.framework', 'Versions', getVersion(), 'lib')
            fw_lib_dir = os.path.join(
                    WORKDIR, '_root', 'Library', 'Frameworks',
                    f'{python_framework_name}.framework', 'Versions', getVersion(), 'lib')
            runCommand(f"cp -a {shellQuote(build_lib_dir)}/* {shellQuote(fw_lib_dir)}")

        print("Copying private frameworks")
        if os.path.exists(workDirFrameworks):
            fw_pvt_fw_dir = os.path.join(
                    WORKDIR, '_root', 'Library', 'Frameworks',
                    f'{python_framework_name}.framework', 'Versions', getVersion())
            runCommand(f"cp -a {shellQuote(workDirFrameworks)} {shellQuote(fw_pvt_fw_dir)}")

    frmDir = os.path.join(ROOTDIR, 'Library', 'Frameworks', f'{python_framework_name}.framework')
    frmDirVersioned = os.path.join(frmDir, 'Versions', getVersion())
    path_to_lib = os.path.join(frmDirVersioned, 'lib', f'python{getVersion()}')

    if main_framework:
        # create directory for OpenSSL certificates
        sslDir = os.path.join(frmDirVersioned, 'etc', 'openssl')
        os.makedirs(sslDir)

    print("Fix file modes")
    gid = grp.getgrnam('admin').gr_gid

    for dirpath, dirnames, filenames in os.walk(frmDir):
        for dn in dirnames:
            os.chmod(os.path.join(dirpath, dn), STAT_0o775, follow_symlinks=False)
            os.chown(os.path.join(dirpath, dn), -1, gid, follow_symlinks=False)

        for fn in filenames:
            if os.path.islink(fn):
                continue
            # "chmod g+w $fn"
            p = os.path.join(dirpath, fn)
            st = os.stat(p, follow_symlinks=False)
            os.chmod(p, stat.S_IMODE(st.st_mode) | stat.S_IWGRP, follow_symlinks=False)
            os.chown(p, -1, gid, follow_symlinks=False)

    LDVERSION=None
    VERSION=None
    ABIFLAGS=None

    fp = open(os.path.join(buildDir, 'Makefile'), 'r')
    for ln in fp:
        if ln.startswith('VERSION='):
            VERSION=ln.split()[1]
        if ln.startswith('ABIFLAGS='):
            ABIFLAGS=ln.split()
            ABIFLAGS=ABIFLAGS[1] if len(ABIFLAGS) > 1 else ''
        if ln.startswith('LDVERSION='):
            LDVERSION=ln.split()[1]
    fp.close()

    LDVERSION = LDVERSION.replace('$(VERSION)', VERSION)
    LDVERSION = LDVERSION.replace('$(ABIFLAGS)', ABIFLAGS)
    config_suffix = '-' + LDVERSION
    if getVersionMajorMinor() >= (3, 6):
        config_suffix = config_suffix + '-darwin'

    # We added some directories to the search path during the configure
    # phase. Remove those because those directories won't be there on
    # the end-users system. Also remove the directories from _sysconfigdata.py
    # (added in 3.3) if it exists.

    include_path = f'-I{WORKDIR}/libraries/usr/local/include'
    lib_path = f'-L{WORKDIR}/libraries/usr/local/lib'

    # fix Makefile
    path = os.path.join(path_to_lib, 'config' + config_suffix, 'Makefile')
    fp = open(path, 'r')
    data = fp.read()
    fp.close()

    for p in (include_path, lib_path):
        data = data.replace(" " + p, '')
        data = data.replace(p + " ", '')

    fp = open(path, 'w')
    fp.write(data)
    fp.close()

    # fix _sysconfigdata
    #
    # TODO: make this more robust!  test_sysconfig_module of
    # distutils.tests.test_sysconfig.SysconfigTestCase tests that
    # the output from get_config_var in both sysconfig and
    # distutils.sysconfig is exactly the same for both CFLAGS and
    # LDFLAGS.  The fixing up is now complicated by the pretty
    # printing in _sysconfigdata.py.  Also, we are using the
    # pprint from the Python running the installer build which
    # may not cosmetically format the same as the pprint in the Python
    # being built (and which is used to originally generate
    # _sysconfigdata.py).

    import pprint
    if getVersionMajorMinor() >= (3, 6):
        # XXX this is extra-fragile
        path = os.path.join(path_to_lib,
            '_sysconfigdata_%s_darwin_darwin.py' % (ABIFLAGS,))
    else:
        path = os.path.join(path_to_lib, '_sysconfigdata.py')
    fp = open(path, 'r')
    data = fp.read()
    fp.close()
    # create build_time_vars dict
    g_dict = {}
    l_dict = {}
    exec(data, g_dict, l_dict)
    build_time_vars = l_dict['build_time_vars']
    vars = {}
    for k, v in build_time_vars.items():
        if isinstance(v, str):
            for p in (include_path, lib_path):
                v = v.replace(' ' + p, '')
                v = v.replace(p + ' ', '')
        vars[k] = v

    fp = open(path, 'w')
    # duplicated from sysconfig._generate_posix_vars()
    fp.write('# system configuration generated and used by'
                ' the sysconfig module\n')
    fp.write('build_time_vars = ')
    pprint.pprint(vars, stream=fp)
    fp.close()

    # For non-main framework variants, remove any file names in its bin
    #   directory that duplicate ones in the main framework's.
    #   TODO: But do not delete the canonical executable name,
    #         i.e. "python3.13", as this currently breaks venv
    #         creation (caused by gh-31958)
    #   [gh-120285] for now, first rename some bin files that are
    #       not currently being suffixed by the main Makefile:
    #           "python3.x-intel64" -> "python3.xt-intel64"
    #   TODO: Handle this in main Makefile ?

    if not main_framework:
        main_framework_bin = os.path.join(ROOTDIR, 'Library', 'Frameworks',
                    'Python.framework', 'Versions', getVersion(), 'bin')
        our_framework_bin = os.path.join(frmDir, 'Versions', getVersion(), 'bin')

        renames = [
            (f'python{getVersion()}-intel64', f'python{getVersion()}t-intel64'),
        ]
        for bin_name in os.listdir(our_framework_bin):
            for bn, new_bin_name in renames:
                if bin_name == bn:
                    print(f'-- renaming bin file: {bin_name} to {new_bin_name}')
                    os.rename(os.path.join(our_framework_bin, bin_name), 
                              os.path.join(our_framework_bin, new_bin_name))
                    break

        for bin_name in (set(os.listdir(main_framework_bin))
                            & set(os.listdir(our_framework_bin))):
            if bin_name == f'python{getVersion()}':
                print(f'-- keeping duplicate bin file: {bin_name}')
            else:
                print(f'-- removing duplicate bin file: {bin_name}')
                os.unlink(os.path.join(our_framework_bin, bin_name))

    os.chdir(curdir)

def createUsrLocalBinLinks():
    # Add symlinks in /usr/local/bin, using relative links.
    # We need to do this after all framework variants have been built
    # as the usr/local/bin directory is updated by each framework build.
    # For now, create links for files in the main framework bin directory.

    usr_local_bin = os.path.join(ROOTDIR, 'usr', 'local', 'bin')
    if os.path.exists(usr_local_bin):
        shutil.rmtree(usr_local_bin)
    os.makedirs(usr_local_bin)

    main_framework_bin = os.path.join(ROOTDIR, 'Library', 'Frameworks',
                'Python.framework', 'Versions', getVersion(), 'bin')
    to_framework = os.path.join('..', '..', '..', 'Library', 'Frameworks',
            'Python.framework', 'Versions', getVersion(), 'bin')

    for fn in os.listdir(main_framework_bin):
        os.symlink(os.path.join(to_framework, fn),
                   os.path.join(usr_local_bin, fn))

    # TODO: for now, also create links for python3.xt and python3.xt-config
    #       in case the user also elects to install the free-threading variant
    to_framework = os.path.join('..', '..', '..', 'Library', 'Frameworks',
            'PythonT.framework', 'Versions', getVersion(), 'bin')
    file_names = [f'python{getVersion()}t', f'python{getVersion()}t-config']
    for fn in file_names:
        os.symlink(os.path.join(to_framework, fn),
                   os.path.join(usr_local_bin, fn))

def patchFile(inPath, outPath):
    data = fileContents(inPath)
    data = data.replace('$FULL_VERSION', getFullVersion())
    data = data.replace('$VERSION', getVersion())
    data = data.replace('$MACOSX_DEPLOYMENT_TARGET', ''.join((DEPTARGET, ' or later')))
    data = data.replace('$ARCHITECTURES', ", ".join(universal_opts_map[UNIVERSALARCHS]))
    data = data.replace('$INSTALL_SIZE', installSize())
    data = data.replace('$THIRD_PARTY_LIBS', "\\\n".join(THIRD_PARTY_LIBS))

    # This one is not handy as a template variable
    data = data.replace('$PYTHONFRAMEWORKINSTALLDIR', '/Library/Frameworks/Python.framework')
    fp = open(outPath, 'w')
    fp.write(data)
    fp.close()

def patchScript(inPath, outPath):
    major, minor = getVersionMajorMinor()
    data = fileContents(inPath)
    data = data.replace('@PYMAJOR@', str(major))
    data = data.replace('@PYVER@', getVersion())
    fp = open(outPath, 'w')
    fp.write(data)
    fp.close()
    os.chmod(outPath, STAT_0o755)

def packageFromRecipe(targetDir, recipe):
    curdir = os.getcwd()
    try:
        # The major version (such as 2.5) is included in the package name
        # because having two version of python installed at the same time is
        # common.
        pkgname = '%s-%s'%(recipe['name'], getVersion())
        srcdir  = recipe.get('source')
        pkgroot = recipe.get('topdir', srcdir)
        postflight = recipe.get('postflight')
        readme = textwrap.dedent(recipe['readme'])
        isRequired = recipe.get('required', True)

        print("- building package %s"%(pkgname,))

        # Substitute some variables
        textvars = dict(
            VER=getVersion(),
            FULLVER=getFullVersion(),
        )
        readme = readme % textvars

        if pkgroot is not None:
            pkgroot = pkgroot % textvars
        else:
            pkgroot = '/'

        if srcdir is not None:
            srcdir = os.path.join(WORKDIR, '_root', srcdir[1:])
            srcdir = srcdir % textvars

        if postflight is not None:
            postflight = os.path.abspath(postflight)

        packageContents = os.path.join(targetDir, pkgname + '.pkg', 'Contents')
        os.makedirs(packageContents)

        if srcdir is not None:
            os.chdir(srcdir)
            runCommand("pax -wf %s . 2>&1"%(shellQuote(os.path.join(packageContents, 'Archive.pax')),))
            runCommand("gzip -9 %s 2>&1"%(shellQuote(os.path.join(packageContents, 'Archive.pax')),))
            runCommand("mkbom . %s 2>&1"%(shellQuote(os.path.join(packageContents, 'Archive.bom')),))

        fn = os.path.join(packageContents, 'PkgInfo')
        fp = open(fn, 'w')
        fp.write('pmkrpkg1')
        fp.close()

        rsrcDir = os.path.join(packageContents, "Resources")
        os.mkdir(rsrcDir)
        fp = open(os.path.join(rsrcDir, 'ReadMe.txt'), 'w')
        fp.write(readme)
        fp.close()

        if postflight is not None:
            patchScript(postflight, os.path.join(rsrcDir, 'postflight'))

        vers = getFullVersion()
        major, minor = getVersionMajorMinor()
        pl = dict(
                CFBundleGetInfoString="Python.%s %s"%(pkgname, vers,),
                CFBundleIdentifier='org.python.Python.%s'%(pkgname,),
                CFBundleName='Python.%s'%(pkgname,),
                CFBundleShortVersionString=vers,
                IFMajorVersion=major,
                IFMinorVersion=minor,
                IFPkgFormatVersion=0.10000000149011612,
                IFPkgFlagAllowBackRev=False,
                IFPkgFlagAuthorizationAction="RootAuthorization",
                IFPkgFlagDefaultLocation=pkgroot,
                IFPkgFlagFollowLinks=True,
                IFPkgFlagInstallFat=True,
                IFPkgFlagIsRequired=isRequired,
                IFPkgFlagOverwritePermissions=False,
                IFPkgFlagRelocatable=False,
                IFPkgFlagRestartAction="NoRestart",
                IFPkgFlagRootVolumeOnly=True,
                IFPkgFlagUpdateInstalledLanguages=False,
            )
        writePlist(pl, os.path.join(packageContents, 'Info.plist'))

        pl = dict(
                    IFPkgDescriptionDescription=readme,
                    IFPkgDescriptionTitle=recipe.get('long_name', "Python.%s"%(pkgname,)),
                    IFPkgDescriptionVersion=vers,
                )
        writePlist(pl, os.path.join(packageContents, 'Resources', 'Description.plist'))

    finally:
        os.chdir(curdir)


def makeMpkgPlist(path):

    vers = getFullVersion()
    major, minor = getVersionMajorMinor()

    pl = dict(
            CFBundleGetInfoString="Python %s"%(vers,),
            CFBundleIdentifier='org.python.Python',
            CFBundleName='Python',
            CFBundleShortVersionString=vers,
            IFMajorVersion=major,
            IFMinorVersion=minor,
            IFPkgFlagComponentDirectory="Contents/Packages",
            IFPkgFlagPackageList=[
                dict(
                    IFPkgFlagPackageLocation='%s-%s.pkg'%(item['name'], getVersion()),
                    IFPkgFlagPackageSelection=item.get('selected', 'selected'),
                )
                for item in pkg_recipes()
            ],
            IFPkgFormatVersion=0.10000000149011612,
            IFPkgFlagBackgroundScaling="proportional",
            IFPkgFlagBackgroundAlignment="left",
            IFPkgFlagAuthorizationAction="RootAuthorization",
        )

    writePlist(pl, path)


def buildInstaller():

    # Zap all compiled files
    for dirpath, _, filenames in os.walk(os.path.join(WORKDIR, '_root')):
        for fn in filenames:
            if fn.endswith('.pyc') or fn.endswith('.pyo'):
                os.unlink(os.path.join(dirpath, fn))

    outdir = os.path.join(WORKDIR, 'installer')
    if os.path.exists(outdir):
        shutil.rmtree(outdir)
    os.mkdir(outdir)

    pkgroot = os.path.join(outdir, 'Python.mpkg', 'Contents')
    pkgcontents = os.path.join(pkgroot, 'Packages')
    os.makedirs(pkgcontents)
    for recipe in pkg_recipes():
        packageFromRecipe(pkgcontents, recipe)

    rsrcDir = os.path.join(pkgroot, 'Resources')

    fn = os.path.join(pkgroot, 'PkgInfo')
    fp = open(fn, 'w')
    fp.write('pmkrpkg1')
    fp.close()

    os.mkdir(rsrcDir)

    makeMpkgPlist(os.path.join(pkgroot, 'Info.plist'))
    pl = dict(
                IFPkgDescriptionTitle="Python",
                IFPkgDescriptionVersion=getVersion(),
            )

    writePlist(pl, os.path.join(pkgroot, 'Resources', 'Description.plist'))
    for fn in os.listdir('resources'):
        if fn == '.svn': continue
        if fn.endswith('.jpg'):
            shutil.copy(os.path.join('resources', fn), os.path.join(rsrcDir, fn))
        else:
            patchFile(os.path.join('resources', fn), os.path.join(rsrcDir, fn))


def installSize(clear=False, _saved=[]):
    if clear:
        del _saved[:]
    if not _saved:
        data = captureCommand("du -ks %s"%(
                    shellQuote(os.path.join(WORKDIR, '_root'))))
        _saved.append("%d"%((0.5 + (int(data.split()[0]) / 1024.0)),))
    return _saved[0]


def savePkgName():
    # TODO: this used to build the bundle package installer dmg image but
    #   it now just passes the desired file name for the .pkg installer along
    #   in a file to be used by the legacy shell scripts that actually
    #   create the pkg installer. This should not be needed in the future.

    outdir = os.path.join(WORKDIR, 'diskimage')
    if os.path.exists(outdir):
        shutil.rmtree(outdir)

    # We used to use the deployment target as the last characters of the
    # installer file name. With the introduction of weaklinked installer
    # variants, we may have two variants with the same file name, i.e.
    # both ending in '10.9'.  To avoid this, we now use the major/minor
    # version numbers of the macOS version we are building on.
    # Also, as of macOS 11, operating system version numbering has
    # changed from three components to two, i.e.
    #   10.14.1, 10.14.2, ...
    #   10.15.1, 10.15.2, ...
    #   11.1, 11.2, ...
    #   12.1, 12.2, ...
    # (A further twist is that, when running on macOS 11, binaries built
    # on older systems may be shown an operating system version of 10.16
    # instead of 11.  We should not run into that situation here.)
    # Also we should use "macos" instead of "macosx" going forward.
    #
    # To maintain compatibility for legacy variants, the file name for
    # builds on macOS 10.15 and earlier remains:
    #   python-3.x.y-macosx10.z.{dmg->pkg}
    #   e.g. python-3.9.4-macosx10.9.{dmg->pkg}
    # and for builds on macOS 11+:
    #   python-3.x.y-macosz.{dmg->pkg}
    #   e.g. python-3.9.4-macos11.{dmg->pkg}

    build_tuple = getBuildTuple()
    if build_tuple[0] < 11:
        os_name = 'macosx'
        build_system_version = '%s.%s' % build_tuple
    else:
        os_name = 'macos'
        # TODO: for now, maintain .pkg file name compatibility
        if build_tuple[0] > 11:
            build_system_version = '11'
        else:
            build_system_version = str(build_tuple[0])
    imagepath = f'python-{getFullVersion()}-{os_name}{build_system_version}.pkg'

    os.mkdir(outdir)
    with open(os.path.join(outdir, "export_installer_pkg_name"), "w") as f:
        f.write(f'export INSTALL_PKG="{imagepath}"\n')
    return


def setIcon(filePath, icnsPath):
    """
    Set the custom icon for the specified file or directory.
    """

    dirPath = os.path.normpath(os.path.dirname(__file__))
    toolPath = os.path.join(dirPath, "seticon.app/Contents/MacOS/seticon")
    if not os.path.exists(toolPath) or os.stat(toolPath).st_mtime < os.stat(dirPath + '/seticon.m').st_mtime:
        # NOTE: The tool is created inside an .app bundle, otherwise it won't work due
        # to connections to the window server.
        appPath = os.path.join(dirPath, "seticon.app/Contents/MacOS")
        if not os.path.exists(appPath):
            os.makedirs(appPath)
        runCommand("cc -o %s %s/seticon.m -framework Cocoa"%(
            shellQuote(toolPath), shellQuote(dirPath)))

    runCommand("%s %s %s"%(shellQuote(os.path.abspath(toolPath)), shellQuote(icnsPath),
        shellQuote(filePath)))

def main():
    # First parse options and check if we can perform our work
    parseOptions()
    checkEnvironment()

    os.environ['MACOSX_DEPLOYMENT_TARGET'] = DEPTARGET
    os.environ['CC'] = CC
    os.environ['CXX'] = CXX
    os.environ['LC_ALL'] = 'C'

    THIRD_PARTY_LIBRARIES_CACHE = os.path.join(DEPSRC, "libraries-saved")

    if os.path.exists(WORKDIR):
        shutil.rmtree(WORKDIR)
    os.mkdir(WORKDIR)

    # cached build of third-party libraries exists?
    if os.path.exists(THIRD_PARTY_LIBRARIES_CACHE):
        print(f" -- WARNING: using pre-built third-party libraries from {THIRD_PARTY_LIBRARIES_CACHE}\n")        
        runCommand(f'cp -a {THIRD_PARTY_LIBRARIES_CACHE} {os.path.join(WORKDIR, "libraries")}')
    else:
        # Build third-party libraries
        buildLibraries()

    # make a copy of third-party libraries for possible reuse
    runCommand(f'cp -a {os.path.join(WORKDIR, "libraries")} {os.path.join(WORKDIR, "libraries-saved")}')

    # Now build python itself
    os.mkdir(ROOTDIR)
    os.mkdir(os.path.join(ROOTDIR, 'empty-dir'))

    # first build the main Python.framework and install
    #   shared third-party libs there
    buildDir = os.path.join(WORKDIR, '_bld', 'python')
    if os.path.exists(buildDir):
        shutil.rmtree(buildDir)
    os.makedirs(buildDir)
    buildPythonFramework("Python", buildDir, "")

    # then build the free-threaded PythonT.framework
    buildDir = os.path.join(WORKDIR, '_bld', 'pythont')
    if os.path.exists(buildDir):
        shutil.rmtree(buildDir)
    os.makedirs(buildDir)
    buildPythonFramework("PythonT", buildDir, "--disable-gil")

    # And then build the documentation
    del os.environ['MACOSX_DEPLOYMENT_TARGET']
    buildPythonDocs()

    # Prepare the symlinks for /usr/local/bin
    createUsrLocalBinLinks()

    # Prepare the applications folder
    folder = os.path.join(WORKDIR, "_root", "Applications", "Python %s"%(
        getVersion(),))
    fn = os.path.join(folder, "License.rtf")
    patchFile("resources/License.rtf",  fn)
    fn = os.path.join(folder, "ReadMe.rtf")
    patchFile("resources/ReadMe.rtf",  fn)
    fn = os.path.join(folder, "Update Shell Profile.command")
    patchScript("scripts/postflight.patch-profile",  fn)
    fn = os.path.join(folder, "Install Certificates.command")
    patchScript("resources/install_certificates.command",  fn)
    os.chmod(folder, STAT_0o755)
    setIcon(folder, "../Icons/Python Folder.icns")

    # Create the installer layout
    buildInstaller()

    # And save the pkg file name for the legacy scripts that do the work
    savePkgName()

if __name__ == "__main__":
    main()
