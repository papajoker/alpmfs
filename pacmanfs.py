#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()

log = logging.getLogger(__name__)

class Fields(Enum):
    VERSION = 0
    NAME = 1
    DESC = 2
    DB = 3
    INSTALL = 4
    BASE = 5

    def ext(self):
        return str(self.name).lower()

    def set_filename(self, pkg):
        """ virtual files : set file names """
        data = f"{pkg.name}.{self.ext()}"
        if self == self.VERSION:
            data = f"{pkg.version}.{self.ext()}"
        if self == self.BASE:
            data = f"{pkg.base}.{self.ext()}"
        if self == self.DB:
            data = f"{pkg.db.name}.{self.ext()}"
        if self == self.INSTALL:
            label = "explicit" if pkg.reason == 0 else "asdependency"
            data = f"{label}.{self.ext()}"
        return data

class AlpmFile():
    def __init__(self, pkg, inode):
        self.name = pkg.name
        self.st_time = pkg.installdate * 1e9
        self.st_size = pkg.isize
        self.inode = pyfuse3.ROOT_INODE + inode +1
        self.st_nlink = 0

    @property
    def st_mode(self):
        return (stat.S_IFDIR | 0o554)

class AlpmLocal():
    def __init__(self):
        self.handle = Handle('/', '/var/lib/pacman')
        self.pkgs = []
        for i, pkg in enumerate(self.handle.get_localdb().pkgcache):
            self.pkgs.append(AlpmFile(pkg, i))

    def get_inode(self, inode):
        r = [f for f in self.pkgs if f.inode == inode]
        try:
            return r[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None # root ?

    def get_file(self, filename):
        r = [f for f in self.pkgs if f"{f.name}" == filename]
        try:
            return r[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None # root ?

    def display_file(self, node, field_id: int):
        p = self.handle.get_localdb().get_pkg(node.name)

        # for demo:
        if field_id == Fields.VERSION.value:
            return f"{p.version}\n"
        #print(dir(p))
        reason = ''
        if p.reason == 0:
            reason = 'Explicitly installed'
        strtime = time.strftime("%a %d %b %Y %X %Z", time.localtime(p.installdate))

        data = f"#{field_id}\n"
        data = f"{data}\n{p.name}\n{p.version}\n{p.desc}\n{p.url}\n\ninstalldate: {strtime}\nDb: {p.db.name}\nInstall reason: {reason}\nDependencies: \n{p.depends}\n"
        if  p.optdepends:
            data += '\n optionals:'
            for opt in p.optdepends:
                data += f"\n\t{opt}"
        return data

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
            entry.st_mode = (stat.S_IFDIR | 0o554)
            entry.st_size = 0
            stamp = int(time.time() * 1e9)
        else:
            pkg = self.packages.get_inode(inode)
            if not pkg:
                return entry
            entry.st_mode = pkg.st_mode
            entry.st_size = pkg.st_size
            stamp = int(pkg.st_time)

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
        entry = pyfuse3.EntryAttributes()
        pkg = self.packages.get_inode(inode)
        entry.st_size = 4000 # less that EntryAttributes.st_blksize 4096
        stamp = int(pkg.st_time)
        entry.st_mode = (stat.S_IFREG | 0o644)
        entry.st_atime_ns = stamp
        entry.st_ctime_ns = stamp
        entry.st_mtime_ns = stamp
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        entry.st_ino = offset

        return entry

    async def lookup(self, parent_inode, name, ctx=None):
        """
            .git .gitignore .directory ...
            and symlinks !
        """
        name = Path(name.decode())
        '''if parent_inode > 1:
            inode, field_id = self.virtual_inode(parent_inode)
            print('vinode', inode, 'field', field_id)'''
        if parent_inode > 1 and name.suffix == ".dep":
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
        pkg = self.packages.get_inode(start_id)
        #print('readdir',fh, 'off', start_id)
        if fh == pyfuse3.ROOT_INODE:
            for pkg in self.packages.pkgs:
                if pkg.inode <= start_id:
                    continue
                name = f"{pkg.name}"
                if not pyfuse3.readdir_reply(token, f"{name}".encode(), await self.getattr(pkg.inode), pkg.inode):
                    break
        else:
            pkg = self.packages.get_inode(fh)
            if not pkg:
                return
            #else:
            #    print('  node name', fh, pkg.name, 'offset:',start_id)
            # yay = 1198
            p = self.packages.handle.get_localdb().get_pkg(pkg.name)
            print(dir(p))
            offset = fh * 100000
            if start_id >= offset:
                return
            
            # generate virtual files
            for vfile in Fields:
                if vfile == Fields.BASE and p.name == p.base:
                    continue
                fname = vfile.set_filename(p).encode()
                if not pyfuse3.readdir_reply(token, fname, await self.get_virtual_attr(fh, offset), offset):
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
        pkg = self.packages.get_inode(inode)
        if pkg:
            return f"{self.path}/{pkg.name}".encode()
        raise pyfuse3.FUSEError(errno.ENOENT)

    @staticmethod
    def virtual_inode(inode):
        """ convert inode by inode_parent_package + field_id """
        if inode > 90000:
            return round(inode/100000), int(inode - (round(inode/100000)*100000))
        return inode, None

    async def read(self, inode, off, size):
        """
        TODO: best display
        view for fields /usr/lib/python3.7/site-packages/pycman/pkginfo.py
        # TO_FIX : always local.db
        # TODO TO_FIX : read file only at first pass, second empty and 3° no acces !!!
        """
        print('v-read', inode, off, size)
        inode, field_id = self.virtual_inode(inode)
        print('   read real ', inode, 'field:', field_id, 'off', off, 'size', size)

        pkg = self.packages.get_inode(inode)
        if not pkg:
            print('   ERROR: file not found', inode)
            return b''

        data = self.packages.display_file(pkg, field_id)
        data = data.encode()
        return data[off:off+size]

    async def open(self, inode, flags, ctx):
        if flags & os.O_RDWR or flags & os.O_WRONLY:
            print("raise open", inode)
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
    parser.add_argument('--debug', action='store_true', default=False,
                        help='Enable debugging output')
    parser.add_argument('--debug-fuse', action='store_true', default=False,
                        help='Enable FUSE debugging output')
    return parser.parse_args()


def main():
    """ fuse mount """
    options = parse_args()
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
