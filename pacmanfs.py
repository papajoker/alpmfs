#!/usr/bin/env python3

'''
pamac build python-pyfuse3 # 7 packages for 3.5 Mo
run : 
    ./pacmanfs.py --debug test
    ./pacmanfs.py --debug ~/pacman

'''

import os
import time
from pathlib import Path
from argparse import ArgumentParser
import stat
import logging
from enum import Enum
import errno
import pyfuse3
#from pyfuse3 import FUSEError
import trio
from pyalpm import Handle
from pycman import config

try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()


log = logging.getLogger(__name__)
USE_APPSTREAM = True

class Fields(Enum):
    """ Fields in Alpm class """
    DIRECTORY = 0
    VERSION = 1
    PACKAGER = 2
    DESC = 3
    DB = 4
    INSTALL = 5
    BASE = 6
    URL = 7
    BACKUP = 8

    def ext(self):
        return str(self.name).lower()


class VirtualFile():
    """ files in a package directory """
    def __init__(self, field: Fields, node):
        self.pkg = None
        self.node = node
        self.field = field

    @classmethod
    def factory(cls, field: Fields, node):
        fclass = globals()[f"Virtual{Fields(field).name.capitalize()}"]
        return fclass(field, node)

    @property
    def filename(self):
        """ virtual files : set file names """
        data = f"{self.pkg.name}.{Fields(self.field).ext()}"
        #data = Fields(self.field).set_filename(self.pkg, self.node)
        return data

    async def get_attr(self, inode, offset, ctx=None):
        entry = pyfuse3.EntryAttributes()
        entry.st_size = 4000 # less that EntryAttributes.st_blksize 4096
        stamp = int(self.node.st_time)
        entry.st_mode = (stat.S_IFREG | 0o444)
        entry.st_atime_ns = stamp
        entry.st_ctime_ns = stamp
        entry.st_mtime_ns = stamp
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        entry.st_ino = offset
        return entry

    @staticmethod
    def get_default_browser():
        import webbrowser
        return webbrowser.get().name

    @property
    def data(self):
        """ content files """
        """
        TODO: best display
        view for fields /usr/lib/python3.7/site-packages/pycman/pkginfo.py
        """
        # for demo:
        if not self.pkg:
            return ""

        #print(dir(p))
        reason = 'dependency'
        if self.pkg.reason == 0:
            reason = 'Explicitly installed'
        strtime = time.strftime("%a %d %b %Y %X %Z", time.localtime(self.pkg.installdate))

        #data = f"#{self.field} {type(self).__name__}\n"
        data = f"{self.pkg.name}\n{self.pkg.version}\n{self.pkg.desc}\n{self.pkg.url}\n\ninstalldate: {strtime}\n" + \
            f"Db: {self.node.repo}\nInstall reason: {reason}\nDependencies: \n{self.pkg.depends}\n"
        if self.pkg.optdepends:
            data += '\nOptionals:'
            for opt in self.pkg.optdepends:
                data += f"\n\t{opt}"
        if self.pkg.backup:
            data += "\n\nBackups:"
            for backup in self.pkg.backup:
                data += f"\n\t/{backup[0]}"
        return data.encode()

class VirtualFileEmpty(VirtualFile):
    @property
    def data(self):
        return b""

    async def get_attr(self, inode, offset, ctx=None):
        entry = await super().get_attr(inode, offset, ctx)
        entry.st_size = 0
        entry.st_mode = (stat.S_IFREG | 0o444)
        return entry

class VirtualDirectory(VirtualFile):
    """ for dolphin """
    @property
    def data(self):
        return f"[Desktop Entry]\nIcon={self.node.ico}\n".encode()

    @property
    def filename(self):
        return ".directory"

    async def get_attr(self, inode, offset, ctx=None):
        entry = await super().get_attr(inode, offset, ctx)
        entry.st_size = 412
        return entry


class VirtualDesc(VirtualFile):
    @property
    def filename(self):
        return f"{self.node.name}.txt"


class VirtualVersion(VirtualFile):
    @property
    def data(self):
        return f"{self.pkg.version}".encode()

    @property
    def filename(self):
        return f"{self.pkg.version}.version"

    async def get_attr(self, inode, offset, ctx=None):
        entry = await super().get_attr(inode, offset, ctx)
        entry.st_size = 120
        entry.st_mode = (stat.S_IFREG | 0o444)
        return entry


class VirtualUrl(VirtualFile):
    """ for thunar """
    @property
    def data(self):
        data = f"Name={self.pkg.name} url\nIcon={self.node.ico}\nTerminal=false\nType=Application\n" + \
            f"Exec={self.get_default_browser()} \"{self.pkg.url}\""
        return f"[Desktop Entry]\n{data}\n".encode()

    @property
    def filename(self):
        return "url.desktop"

    async def get_attr(self, inode, offset, ctx=None):
        entry = await super().get_attr(inode, offset, ctx)
        entry.st_size = 1280
        entry.st_mode = (stat.S_IFREG | 0o555)
        return entry


class VirtualPackager(VirtualFile):
    @property
    def filename(self):
        try:
            label = self.pkg.packager.split('<')[0:][0]
        except IndexError:
            label = self.pkg.packager
        return f"{label.strip()}.packager"

    @property
    def data(self):
        return f"{self.pkg.packager}".encode()

    async def get_attr(self, inode, offset, ctx=None):
        entry = await super().get_attr(inode, offset, ctx)
        entry.st_size = 256
        entry.st_mode = (stat.S_IFREG | 0o555)
        return entry

class VirtualDb(VirtualFileEmpty):
    @property
    def filename(self):
        return f"{self.node.repo}.db"


class VirtualInstall(VirtualFileEmpty):
    @property
    def filename(self):
        label = "explicit" if self.pkg.reason == 0 else "asdependency"
        return f"{label}.install"


class VirtualBase(VirtualFileEmpty):
    @property
    def filename(self):
        label = "explicit" if self.pkg.reason == 0 else "asdependency"
        return f"{self.pkg.base}.base"

class VirtualBackup(VirtualFile):
    def get_backup_filename(self):
        if self.pkg:
            for backup in self.pkg.backup:
                return f"/{backup[0]}"
        return None

    @property
    def data(self):
        filename = self.get_backup_filename()
        if not filename:
            return b""
        try:
            with open(filename, 'rb') as bfile:
                data = bfile.read()
                return data
        except PermissionError:
            return b""

    @property
    def filename(self):
        label = self.get_backup_filename().replace('/',' ')
        return f"{label[1:]}.backup"

    async def get_attr(self, inode, offset, ctx=None):
        filename = self.get_backup_filename()
        entry = await super().get_attr(inode, offset, ctx)
        if filename:
            entry.st_size = os.stat(filename).st_size
        return entry

class AlpmFile():
    def __init__(self, pkg, inode, repo='local'):
        self.name = pkg.name
        self.repo = repo
        self.st_time = pkg.installdate * 1e9
        self.st_size = pkg.isize
        self.inode = pyfuse3.ROOT_INODE + inode +1
        self.st_nlink = 0
        self.ico = "package"
        #print(self.repo)

    @property
    def st_mode(self):
        return (stat.S_IFDIR | 0o555)


class AlpmLocal():
    def __init__(self):
        #  /usr/share/gir-1.0/AppStreamGlib-1.0.gir
        try:
            if not USE_APPSTREAM:
                raise ValueError
            import gi
            gi.require_version('AppStreamGlib', '1.0')
            from gi.repository import AppStreamGlib
            app_store = AppStreamGlib.Store()
            app_store.load(flags=AppStreamGlib.StoreLoadFlags.APP_INFO_SYSTEM);
            app_store.set_search_match(AppStreamGlib.AppSearchMatch.PKGNAME | AppStreamGlib.AppSearchMatch.NAME |  AppStreamGlib.AppSearchMatch.KEYWORD);
        except ValueError:
            app_store = None

        def _app_store_ico(tofind):
            """ find app in store
            It's very slow ...
            """
            default = "package"
            for app in app_store.get_apps():
                if app.get_kind() != AppStreamGlib.AppKind.DESKTOP:
                    continue
                if app.get_pkgname_default() == tofind:
                    #print(f"-- AppStream trouvé -- {tofind}")
                    # FIX entries errors
                    iname = app.get_icon_default().get_name()
                    if not ".png" in iname:
                        iname = f"{app.get_pkgname_default()}_{iname}.png"
                    icon = f"{app.get_icon_path()}/64x64/{iname}"
                    if not Path(icon).exists():
                        print(f"  bad ico ? {icon} {app.get_icon_default().get_name()}")
                        icon = default
                    return icon
            return default

        #self.handle = Handle('/', '/var/lib/pacman')
        self.handle = config.init_with_config("/etc/pacman.conf")
        self.pkgs = []
        for i, pkg in enumerate(self.handle.get_localdb().pkgcache):
            pkg_repo = self._find(pkg.name)
            afile = AlpmFile(pkg, i, pkg_repo)
            if app_store:
                afile.ico = _app_store_ico(pkg.name)
            self.pkgs.append(afile)
        print(f"end scan {len(self.pkgs)} packages")

    def _find(self, pkg_name):
        """find one package in db"""
        for db_repo in self.handle.get_syncdbs():
            pkg = db_repo.get_pkg(pkg_name)
            if pkg:
                return db_repo.name
        return 'local'

    def get_inode(self, inode):
        nodes = [f for f in self.pkgs if f.inode == inode]
        try:
            return nodes[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None # root ?

    def get_file(self, filename):
        filenames = [f for f in self.pkgs if f"{f.name}" == filename]
        try:
            return filenames[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None # root ?


class AlpmFs(pyfuse3.Operations):
    def __init__(self, path):
        self.path = path
        self.packages = AlpmLocal()
        super(AlpmFs, self).__init__()

    async def getattr(self, inode, ctx=None):
        """ return file attributes """
        if inode > 90000:
            vinode, _ = self.virtual_inode(inode)
            return await self.get_virtual_attr(vinode, inode, ctx)
        entry = pyfuse3.EntryAttributes()
        if inode < pyfuse3.ROOT_INODE+1:
            entry.st_mode = (stat.S_IFDIR | 0o555)
            entry.st_size = 0
            stamp = int(time.time() * 1e9)
        else:
            node = self.packages.get_inode(inode)
            if not node:
                return entry
            entry.st_mode = node.st_mode
            entry.st_size = node.st_size
            stamp = int(node.st_time)

        entry.st_atime_ns = stamp
        entry.st_ctime_ns = stamp
        entry.st_mtime_ns = stamp
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        entry.st_ino = inode
        entry.entry_timeout = 10
        entry.attr_timeout = 10

        return entry

    async def get_virtual_attr(self, inode, offset, ctx=None):
        node = self.packages.get_inode(inode)
        virtual = VirtualFile.factory(offset % 20, node)
        return await virtual.get_attr(inode, offset, ctx)

    async def lookup(self, parent_inode, name, ctx=None):
        """
            .git .gitignore .directory ...
            and symlinks !
        """
        name = Path(name.decode())
        if parent_inode > 1 and (name.suffix in [".dep"]):
            name = name.stem
            #print('   lookup symlink ', parent_inode, name)
        pkg = self.packages.get_file(str(name))
        if not pkg:
            '''print("   not found !!!", name)
            entry = pyfuse3.EntryAttributes()
            entry.st_ino = 0
            return entry'''
            raise pyfuse3.FUSEError(errno.ENOENT)
        return await self.getattr(pkg.inode)

    async def opendir(self, inode, ctx):
        return inode

    async def readdir(self, fh, start_id, token):
        node = self.packages.get_inode(start_id)
        #print('readdir',fh, 'off', start_id)
        if fh == pyfuse3.ROOT_INODE:
            for node in self.packages.pkgs:
                if node.inode <= start_id:
                    continue
                name = f"{node.name}"
                if not pyfuse3.readdir_reply(token, f"{name}".encode(), await self.getattr(node.inode), node.inode):
                    break
        else:
            node = self.packages.get_inode(fh)
            if not node:
                return
            #else:
            #    print('  node name', fh, pkg.name, 'offset:',start_id)
            # yay = 1198
            p = self.packages.handle.get_localdb().get_pkg(node.name)
            offset = fh * 100000
            if start_id >= offset:
                return

            # generate virtual files
            for vfile in Fields:
                virtual = VirtualFile.factory(vfile.value, node)
                virtual.pkg = p
                if vfile == Fields.BASE and p.name == p.base:
                    offset += 1
                    continue
                if vfile == Fields.BACKUP and not p.backup:
                    offset += 1
                    continue
                if not pyfuse3.readdir_reply(token, virtual.filename.encode(), await virtual.get_attr(fh, offset), offset):
                    return
                offset += 1

            # generate symlinks : Dependencies (and optionals ?)
            for dep in p.depends:
                deps = dep.split('>', 1)
                link = self.packages.get_file(deps[:1][0])
                if not link:
                    return
                offset += 1
                mode = await self.getattr(link.inode)
                mode.st_mode = (stat.S_IFLNK | 0o555)
                mode.st_nlink = link.inode
                #link.st_nlink += 1 # NO ! increment at all directory read !
                if not pyfuse3.readdir_reply(token, f"{link.name}.dep".encode(), mode, offset):
                    return

        return

    async def readlink(self, inode, ctx):
        """ set target to link """
        node = self.packages.get_inode(inode)
        if node:
            return f"{self.path}/{node.name}".encode()
        raise pyfuse3.FUSEError(errno.ENOENT)

    @staticmethod
    def virtual_inode(inode):
        """ convert inode by inode_parent_package + field_id """
        if inode > 90000:
            return round(inode/100000), int(inode % 20)
        return inode, None

    async def read(self, inode, off, size):
        """ read content virtual file """
        #log.info(f"v-read: inode:{inode} {off} {size}")
        inode, field_id = self.virtual_inode(inode)
        #log.info(f"   read real {inode} field:{field_id} off:{off} size:{size}")

        node = self.packages.get_inode(inode)
        if not node:
            #log.warning(f"   ERROR: file not found {inode}")
            return b''

        virtual = VirtualFile.factory(field_id, node)
        virtual.pkg = self.packages.handle.get_localdb().get_pkg(node.name)
        data = virtual.data

        return data[off:off+size]

    async def open(self, inode, flags, ctx):
        if flags & os.O_RDWR or flags & os.O_WRONLY:
            log.error(f"raise open {inode}")
            raise pyfuse3.FUSEError(errno.EPERM)
        return inode

"""
    async def statfs(self, ctx):
        '''Get file system statistics
        *ctx* will be a `RequestContext` instance.
        The method must return an appropriately filled `StatvfsData` instance.
        '''
        print('statfs',ctx)
        stat_ = pyfuse3.StatvfsData()
        return stat_
        raise pyfuse3.FUSEError(errno.ENOSYS)
"""

def init_logging(debug=False):
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d %(threadName)s: '
                                  '[%(name)s] %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    if debug:
        handler.setLevel(logging.DEBUG)
        root_logger.setLevel(logging.DEBUG)
    else:
        handler.setLevel(logging.INFO)
        root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

def parse_args():
    """ Parse command line """

    parser = ArgumentParser()

    parser.add_argument('mountpoint', type=str,
                        help='Where to mount the file system')
    parser.add_argument('--no-appstream', action='store_true', default=False,
                        help='Not use AppStream')
    parser.add_argument('--debug', action='store_true', default=False,
                        help='Enable debugging output')
    parser.add_argument('--debug-fuse', action='store_true', default=False,
                        help='Enable FUSE debugging output')
    return parser.parse_args()


def main():
    """ fuse mount """
    options = parse_args()
    print(options)
    global USE_APPSTREAM
    USE_APPSTREAM = not options.no_appstream

    init_logging(options.debug)
    fuse_options = set(pyfuse3.default_options)
    if options.debug_fuse:
        fuse_options.add('debug')
    options.mountpoint = Path(options.mountpoint).resolve()
    options.mountpoint.mkdir(parents=True, exist_ok=True)

    virtual_fs = AlpmFs(path=str(options.mountpoint))
    pyfuse3.init(virtual_fs, str(options.mountpoint), fuse_options)
    try:
        trio.run(pyfuse3.main)
    except KeyboardInterrupt:
        print(f"\n\nfusermount -u {options.mountpoint}\n")
    except:
        pyfuse3.close(unmount=True)
        print(f"\n\nfusermount -u {options.mountpoint} ok\n")
        raise

    pyfuse3.close(unmount=True)


if __name__ == '__main__':
    main()
