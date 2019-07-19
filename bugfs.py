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


try:
    import faulthandler
except ImportError:
    pass
else:
    faulthandler.enable()


class AlpmFs(pyfuse3.Operations):
    def __init__(self, path):
        self.path = path
        self.dirs = []
        for i in range(1, 500):
            self.dirs.append({"inode":i, "name":str(i)})
        super(AlpmFs, self).__init__()
        self.supports_dot_lookup = False
        self.enable_writeback_cache = False


    def get_node(self, inode):
        nodes = [f for f in self.dirs if f['inode'] == inode]
        try:
            return nodes[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None # root ?

    def get_file(self, pkgname):
        pkgnames = [f for f in self.dirs if f['name'] == pkgname]
        try:
            return pkgnames[0]
        except IndexError:
            #raise pyfuse3.FUSEError(errno.ENOENT)
            return None

    async def getattr(self, inode, ctx=None):
        """ return file attributes """
        #print("getattr", inode)
        node = self.get_node(inode)
        if not node:
            print("  getattrnode ERROR :", node)
        entry = pyfuse3.EntryAttributes()
        #if inode < pyfuse3.ROOT_INODE+100:
        entry.st_mode = (stat.S_IFDIR | 0o555)

        entry.st_size = 4096
        stamp = int(0)
        entry.st_atime_ns = stamp
        entry.st_ctime_ns = stamp
        entry.st_mtime_ns = stamp
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        entry.st_ino = inode
        return entry

    async def lookup(self, parent_inode, name, ctx=None):
        #print("lookup", name, "node parent:", parent_inode)
        name = Path(name.decode())
        if parent_inode > 1 and (name.suffix in [".link"]):
            name = name.stem
            print(f"   lookup symlink {parent_inode} {name}")
        node = self.get_file(str(name))
        if not node:
            print("   not found !!!", name)
            raise pyfuse3.FUSEError(errno.ENOENT)
        return await self.getattr(node['inode'])

    async def opendir(self, inode, ctx):
        return inode

    async def readdir(self, fh, start_id, token):
        #node = self.packages.get_inode(start_id)
        #print('readdir',fh, 'off', start_id)
        '''print(token.size)'''
        if fh == pyfuse3.ROOT_INODE:
            for node in self.dirs:
                if node['inode'] <= start_id:
                    continue
                name = f"{node['name']}"
                if not pyfuse3.readdir_reply(token, f"{name}".encode(), await self.getattr(node['inode']), node['inode']):
                    break
        else:
            node = self.get_node(fh)
            if not node:
                return
            
            DEBUGPKGNAME="vlc"
            if node['name'] == DEBUGPKGNAME:
                print('  node name', fh, node['name'], 'offset:', start_id)
            offset = fh * 100000
            if start_id >= offset:
                return

            # generate symlinks : Dependencies (and optionals ?)
            # BUG TODO readdir_reply return false after 16 links !!!
            if fh > 600: # only one level
                return
            offset += 10
            i = 0 # count bug -> stop always at 17 !!! (vlc, gimp ...)
            for link in range(5, 48):
                if fh == link:
                    continue
                linknode = self.get_file(str(link))
                if not linknode:
                    return
                offset += 1
                mode = await self.getattr(linknode['inode'])
                mode.st_mode = (stat.S_IFLNK | 0o555)
                mode.st_nlink = linknode['inode']
                #print('  link create for:', node['name'], offset, 'target:', linknode['name'], linknode['inode'])
                if not pyfuse3.readdir_reply(token, f"{linknode['name']}.link".encode(), mode, offset):
                    '''
                    print(dir(token))

                    print(f"ERROR readdir_reply (no:{i} , {token['size']}) link: {linknode['name']} inode: {offset} mode_ino: {mode.st_ino} {mode}")
                    '''
                    return
                i += 1
        return

    async def readlink(self, inode, ctx):
        """ set target to link """
        node = self.get_node(inode)
        if node:
            return f"{self.path}/{node['name']}".encode()
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

        node = self.get_inode(inode)
        if not node:
            #log.warning(f"   ERROR: file not found {inode}")
            return b''
        data = b"test"
        return data[off:off+size]

    async def open(self, inode, flags, ctx):
        return inode


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
