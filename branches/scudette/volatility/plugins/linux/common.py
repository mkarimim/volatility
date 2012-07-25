# Volatility
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

"""
@author:       Andrew Case
@license:      GNU General Public License 2.0 or later
@contact:      atcuno@gmail.com
@organization: Digital Forensics Solutions
"""

import volatility.utils    as utils
import volatility.obj      as obj

from volatility import plugin


class AbstractLinuxCommandPlugin(plugin.PhysicalASMixin,
                                 plugin.KernelASMixin,
                                 plugin.ProfileCommand):
    """A base class for all linux based plugins."""
    __abstract = True

    @classmethod
    def is_active(cls, config):
        """We are only active if the profile is linux."""
        return (getattr(config.profile, "_md_os", None) == 'linux' and
                plugin.Command.is_active(config))


class LinuxFindDTB(plugin.PhysicalASMixin, plugin.ProfileCommand):
    """A scanner for DTB values.

    For linux, the dtb values are taken directly from the symbol file. Linux has
    a direct mapping between the kernel virtual address space and the physical
    memory.  This is the difference between the virtual and physical addresses
    (aka PAGE_OFFSET). This is defined by the __va macro:

    #define __va(x) ((void *)((unsigned long) (x) + PAGE_OFFSET))

    This one plugin handles both 32 and 64 bits.
    """

    __name = "find_dtb"

    def dtb_hits(self):
        """Tries to locate the DTB."""
        if self.profile.metadata("memory_model") == "32bit":
            PAGE_OFFSET = (self.profile.get_constant("_text") -
                           self.profile.get_constant("phys_startup_32"))

            yield self.profile.get_constant("swapper_pg_dir") - PAGE_OFFSET
        else:
            PAGE_OFFSET = (self.profile.get_constant("_text") -
                           self.profile.get_constant("phys_startup_64"))

            yield self.profile.get_constant("init_level4_pgt") - PAGE_OFFSET

    def verify_address_space(self, address_space):
        # There is not really much we can do if the address space is wrong, so
        # we just keep going.
        return True

    def render(self, fd = None):
        fd.write("DTB\n")
        for dtb in self.dtb_hits():
            fd.write("{0:#010x}\n".format(dtb))


class LinProcessFilter(AbstractLinuxCommandPlugin):
    """A class for filtering processes."""

    __abstract = True

    def __init__(self, phys_task_struct=None, pids=None, pid=None, **kwargs):
        """Lists information about all the dlls mapped by a process.

        Args:
           phys_task_struct: One or more task structs or offsets defined in
              the physical AS.

           pids: A list of pids.
           pid: A single pid.
        """
        super(LinProcessFilter, self).__init__(**kwargs)

        if isinstance(phys_task_struct, int):
            phys_task_struct = [phys_task_struct]
        elif phys_task_struct is None:
            phys_task_struct = []

        self.phys_task_struct = phys_task_struct

        if pids is None:
            pids = []

        if pid is not None:
            pids.append(pid)

        self.pids = pids

    def filter_processes(self):
        """Filters eprocess list using phys_eprocess and pids lists."""
        # No filtering required:
        if not self.phys_task_struct and not self.pids:
            for task in self.session.plugins.pslist(
                session=self.session).pslist():
                yield task
        else:
            # We need to filter by phys_task_struct
            for offset in self.phys_task_struct:
                yield self.virtual_process_from_physical_offset(offset)

            # We need to filter by pids
            for task in self.session.plugins.pslist(
                session=self.session).pslist():
                if int(task.pid) in self.pids:
                    yield task

    def virtual_process_from_physical_offset(self, physical_offset):
        """Tries to return an eprocess in virtual space from a physical offset.

        We do this by reflecting off the list elements.

        Args:
           physical_offset: The physcial offset of the process.

        Returns:
           an _EPROCESS object or a NoneObject on failure.
        """
        physical_task = self.profile.Object(
            theType="task_struct", offset=int(physical_offset),
            vm=self.kernel_address_space.base)

        # We cast our list entry in the kernel AS by following Flink into the
        # kernel AS and then the Blink. Note the address space switch upon
        # dereferencing the pointer.
        our_list_entry = physical_task.tasks.next.dereference(
            vm=self.kernel_address_space).prev.dereference()

        # Now we get the EPROCESS object from the list entry.
        return our_list_entry.dereference_as("task_struct", "tasks")





# TODO: Deprecate this when all plugins have been converted.
class AbstractLinuxCommand(object):

    def __init__(self, *args, **kwargs):
        self.addr_space = utils.load_as(self._config)
        self.profile = self.addr_space.profile
        self.smap = self.profile.sys_map

    @classmethod
    def is_active(cls, config):
        """We are only active if the profile is windows."""
        try:
            return config.PROFILE and config.PROFILE._md_os == 'linux'
        except profile.Error:
            return True


def offsetof(struct_name, list_member, profile):

    offset = profile.typeDict[struct_name][1][list_member][0]
    return offset

def bit_is_set(bmap, pos):

    mask = 1 << pos
    return bmap & mask

# returns a list of online cpus (the processor numbers)
def online_cpus(smap, addr_space):

    #later kernels..
    if "cpu_online_bits" in smap:
        bmap = obj.Object("unsigned long", offset=smap["cpu_online_bits"], vm=addr_space)

    elif "cpu_present_map" in smap:
        bmap = obj.Object("unsigned long",  offset=smap["cpu_present_map"], vm=addr_space)

    else:
        raise AttributeError, "Unable to determine number of online CPUs for memory capture"

    cpus = []
    for i in xrange(0, 8):
        if bit_is_set(bmap, i):
            cpus.append(i)

    return cpus

def walk_per_cpu_var(obj_ref, per_var, var_type):

    cpus = online_cpus(obj_ref.smap, obj_ref.addr_space)

    # get the highest numbered cpu
    max_cpu = cpus[-1]

    per_offsets = obj.Object(theType='Array', targetType='unsigned long', count=max_cpu, offset=obj_ref.smap["__per_cpu_offset"], vm=obj_ref.addr_space)
    i = 0

    for i in cpus:

        offset = per_offsets[i]

        addr = obj_ref.smap["per_cpu__" + per_var] + offset.v()
        var = obj.Object(var_type, offset=addr, vm=obj_ref.addr_space)

        yield i, var

# similar to for_each_process for this usage
def walk_list_head(struct_name, list_member, list_head_ptr, addr_space):

    list_ptr = list_head_ptr.next
    offset = offsetof(struct_name, list_member, addr_space.profile)

    # this happens in rare instances where list_heads get pre-initlized
    # the caller needs to check for not return value
    # currently only needed by linux_mount when walking mount_hashtable
    if list_ptr == list_head_ptr or not list_ptr:
        return

    while 1:

        # return the address of the beginning of the strucutre, similar to list.h in kernel
        yield obj.Object(struct_name, offset = list_ptr - offset, vm = addr_space)

        list_ptr = list_ptr.next

        if list_ptr == list_head_ptr or not list_ptr:
            break


def walk_internal_list(struct_name, list_member, list_start, addr_space):

    while 1:

        list_struct = obj.Object(struct_name, vm = addr_space, offset = list_start)

        yield list_struct

        list_start = list_struct.__getattribute__(list_member)

        if not list_start:
            break

def get_string(addr, addr_space, maxlen = 256):

    name = addr_space.read(addr, maxlen)
    ret = ""

    for n in name:
        if ord(n) == 0:
            break
        ret = ret + n

    return ret


def format_path(path_list):

    path = '/'.join(path_list)

    return path

def IS_ROOT(dentry):

    return dentry == dentry.d_parent

# based on __d_path
# TODO: (deleted) support
def do_get_path(rdentry, rmnt, dentry, vfsmnt, addr_space):

    ret_path = []

    inode = dentry.d_inode

    while 1:

        dname = get_string(dentry.d_name.name, addr_space)

        if dname != '/':
            ret_path.append(dname)

        if dentry == rdentry and vfsmnt == rmnt:
            break

        if dentry == vfsmnt.mnt_root or IS_ROOT(dentry):
            if vfsmnt.mnt_parent == vfsmnt:
                break
            dentry = vfsmnt.mnt_mountpoint
            vfsmnt = vfsmnt.mnt_parent
            continue

        parent = dentry.d_parent

        dentry = parent

    ret_path.reverse()

    ret_val = format_path(ret_path)

    if ret_val.startswith(("socket:", "pipe:")):
        if ret_val.find("]") == -1:
            ret_val = ret_val[:-1] + "[{0}]".format(inode.i_ino)
        else:
            ret_val = ret_val.replace("/","")

    elif ret_val != "inotify":
        ret_val = '/' + ret_val

    return ret_val

def get_path(task, filp, addr_space):

    rdentry  = task.fs.get_root_dentry()
    rmnt     = task.fs.get_root_mnt()
    dentry = filp.get_dentry()
    vfsmnt = filp.get_vfsmnt()

    return do_get_path(rdentry, rmnt, dentry, vfsmnt, addr_space)

# this is here b/c python is retarded and its inet_ntoa can't handle integers...
def ip2str(ip):

    a = ip & 0xff
    b = (ip >> 8) & 0xff
    c = (ip >> 16) & 0xff
    d = (ip >> 24) & 0xff

    return "%d.%d.%d.%d" % (a, b, c, d)

def ip62str(in6addr):

    ret     = ""
    ipbytes = in6addr.in6_u.u6_addr8
    ctr     = 0

    for byte in ipbytes:
        ret = ret + "%.02x" % byte

        # make it the : notation
        if ctr % 2 and ctr != 15:
            ret = ret + ":"

        ctr = ctr + 1

    return ret

def S_ISDIR(mode):
    return (mode & linux_flags.S_IFMT) == linux_flags.S_IFDIR

def S_ISREG(mode):
    return (mode & linux_flags.S_IFMT) == linux_flags.S_IFREG





