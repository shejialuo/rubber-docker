#!/usr/bin/env python2.7

from __future__ import print_function

import os
import tarfile
import stat
import uuid

import click
import traceback

import linux

# Set the encode mode to be uft-8
import sys
reload(sys)
sys.setdefaultencoding('utf8')

def _get_image_path(image_name, image_dir, image_suffix='tar'):
    return os.path.join(image_dir, os.extsep.join([image_name, image_suffix]))


def _get_container_path(container_id, container_dir, *subdir_names):
    return os.path.join(container_dir, container_id, *subdir_names)


def create_container_root(image_name, image_dir, container_id, container_dir):
    """

    Usage:
    new_root = create_container_root(
        image_name, image_dir, container_id, container_dir)

    @param image_name: the image name to extract
    @param image_dir: the directory to lookup image tarballs in
    @param container_id: the unique container id
    @param container_dir: the base directory of newly generated container
                          directories
    @retrun: new container root directory
    @rtype: str
    """
    image_path = _get_image_path(image_name, image_dir)
    image_root_path = os.path.join(image_dir, image_name, 'rootfs')
    assert os.path.exists(image_path), "unable to locate image %s" % image_name

    # Instead of extracting the image every time, we should store
    # it in the overlay filesystem.
    if not os.path.exists(image_root_path):
        os.makedirs(image_root_path)
        with tarfile.open(image_path) as t:
            # Fun fact: tar files may contain *nix devices! *facepalm*
            members = [m for m in t.getmembers()
                       if m.type not in (tarfile.CHRTYPE, tarfile.BLKTYPE)]
            t.extractall(image_root_path, members=members)

    # Create directories for copy-on-write (uppperdir), overlay workdir
    # See https://www.kernel.org/doc/Documentation/filesystems/overlayfs.txt
    container_cow_rw = _get_container_path(
        container_id, container_dir, 'cow_rw')
    container_cow_workdir = _get_container_path(
        container_id, container_dir, 'cow_workdir')
    container_rootfs = _get_container_path(
        container_id, container_dir, 'rootfs')
    for d in (container_cow_rw, container_cow_workdir, container_rootfs):
        if not os.path.exists(d):
            os.makedirs(d)

    # Here, we use kernel support for overlay. Well, we just use it...
    linux.mount(
        'overlay', container_rootfs, 'overlay', linux.MS_NODEV,
        "lowerdir={image_root},upperdir={cow_rw},workdir={cow_workdir}".format(
            image_root=image_root_path,
            cow_rw=container_cow_rw,
            cow_workdir=container_cow_workdir))

    return container_rootfs  # return the mountpoint for the mounted overlayfs

def create_mounts(new_root_path):
    # We should mount `proc`, `sys` and recursive mount `/dev`.
    # This is like the way as the command line.
    # We could use `cat /proc/filesystems` to see the virtual
    # filesystem
    linux.mount('proc', os.path.join(new_root_path, 'proc'), 'proc', 0, '')
    linux.mount('sysfs', os.path.join(new_root_path, 'sys'), 'sysfs', 0, '')
    linux.mount('tmpfs', os.path.join(new_root_path, 'dev'), 'tmpfs',
                linux.MS_NOSUID | linux.MS_STRICTATIME, 'mode=755')

def make_dev(new_root_path):
    devpts_path = os.path.join(new_root_path, 'dev', 'pts')
    if not os.path.exists(devpts_path):
        os.makedirs(devpts_path)
        linux.mount('devpts', devpts_path, 'devpts', 0, '')
    for i, dev in enumerate(['stdin', 'stdout', 'stderr']):
        os.symlink('/proc/self/fd/%d' % i, os.path.join(new_root_path, 'dev', dev))

    # A device ID consists of two parts: a major ID, identifying the class of the
    # device, and a minor ID, identifying a specific instance of a device in
    # that class. We could use `ls -l` to see the major and minor ID.
    devices = [
      {'name': 'null', 'major': 1, 'minor': 3},
      {'name': 'zero', 'major': 1, 'minor': 5},
      {'name': 'random', 'major': 1, 'minor': 8},
      {'name': 'urandom', 'major': 1, 'minor': 9},
      {'name': 'console', 'major': 5, 'minor': 1},
      {'name': 'tty', 'major': 5, 'minor': 0},
      {'name': 'full', 'major': 1, 'minor': 7},
    ]
    dev_path = os.path.join(new_root_path, 'dev')
    for device in devices:
        device_id = os.makedev(device['major'], device['minor'])
        os.mknod(os.path.join(dev_path, device['name']), 0o666 | stat.S_IFCHR, device_id)

def setup_cpu_cgroup(container_id, cpu_shares):
    CPU_CGROUP_BASEDIR = '/sys/fs/cgroup/cpu'
    container_cpu_cgroup_dir = os.path.join(
        CPU_CGROUP_BASEDIR, 'rubber_docker', container_id)

    # Insert the container to new cpu cgroup named 'rubber_docker/container_id'
    if not os.path.exists(container_cpu_cgroup_dir):
        os.makedirs(container_cpu_cgroup_dir)
    tasks_file = os.path.join(container_cpu_cgroup_dir, 'tasks')
    open(tasks_file, 'w').write(str(os.getpid()))

    # If (cpu_shares != 0)  => set the 'cpu.shares' in our cpu cgroup
    if cpu_shares:
        cpu_shares_file = os.path.join(container_cpu_cgroup_dir, 'cpu.shares')
        open(cpu_shares_file, 'w').write(str(cpu_shares))


def setup_memory_cgroup(container_id, memory, memory_swap):
    MEMORY_CGROUP_BASEDIR = '/sys/fs/cgroup/memory'
    container_mem_cgroup_dir = os.path.join(
        MEMORY_CGROUP_BASEDIR, 'rubber_docker', container_id)

    # Insert the container to new memory cgroup named 'rubber_docker/container_id'
    if not os.path.exists(container_mem_cgroup_dir):
        os.makedirs(container_mem_cgroup_dir)
    tasks_file = os.path.join(container_mem_cgroup_dir, 'tasks')
    open(tasks_file, 'w').write(str(os.getpid()))

    if memory is not None:
        mem_limit_in_bytes_file = os.path.join(
            container_mem_cgroup_dir, 'memory.limit_in_bytes')
        open(mem_limit_in_bytes_file, 'w').write(str(memory))
    if memory_swap is not None:
        memsw_limit_in_bytes_file = os.path.join(
            container_mem_cgroup_dir, 'memory.memsw.limit_in_bytes')
        open(memsw_limit_in_bytes_file, 'w').write(str(memory_swap))

@click.group()
def cli():
    pass


def contain(command, image_name, image_dir, container_id, container_dir,
            cpu_shares, memory, memory_swap, user):

    setup_cpu_cgroup(container_id, cpu_shares)
    setup_memory_cgroup(container_id, memory, memory_swap)

    new_root_path = create_container_root(image_name, image_dir, container_id, container_dir)

    linux.sethostname(container_id)

    # (https://www.kernel.org/doc/Documentation/filesystems/sharedsubtree.txt)
    # Make / a private mount to avoid littering our host mount table.
    # Use `man mount_namespaces`
    # MS_PRIVATE: This mount is private; it does not have a peer group. Mount
    #             and unmount events do not propagate into or out of this mount.
    # MS_REC: We need to recursive does this.
    linux.mount(None, '/', None, linux.MS_PRIVATE | linux.MS_REC, None)

    create_mounts(new_root_path)
    make_dev(new_root_path)

    old_root_path = os.path.join(new_root_path, '.pivot_root')
    os.makedirs(old_root_path)
    linux.pivot_root(new_root_path, old_root_path)
    # After `pivot_root`, the working directory would be corrupted.
    # So we should use `os.chdir`to change the working directory
    os.chdir('/')

    # Now `old_root_path` should be changed to `./.pivot_root`
    old_root_path = './.pivot_root'
    linux.umount2(old_root_path, linux.MNT_DETACH)
    os.rmdir(old_root_path)

    os.execvp(command[0], command)


@cli.command(context_settings=dict(ignore_unknown_options=True,))
@click.option('--memory',
              help='Memory limit in bytes.'
              ' Use suffixes to represent larger units (k, m, g)',
              default=None)
@click.option('--memory-swap',
              help='A positive integer equal to memory plus swap.'
              ' Specify -1 to enable unlimited swap.',
              default=None)
@click.option('--cpu-shares', help='CPU shares (relative weight)', default=0)
@click.option('--user', help='UID (format: <uid>[:<gid>])', default='')
@click.option('--image-name', '-i', help='Image name', default='ubuntu')
@click.option('--image-dir', help='Images directory',
              default='/workshop/images')
@click.option('--container-dir', help='Containers directory',
              default='/workshop/containers')
@click.argument('Command', required=True, nargs=-1)
def run(memory, memory_swap, cpu_shares, user,
        image_name, image_dir, container_dir, command):
    container_id = str(uuid.uuid4())

    flags = linux.CLONE_NEWNS | linux.CLONE_NEWUTS | linux.CLONE_NEWPID | linux.CLONE_NEWNET
    contain_args = (command, image_name, image_dir, container_id,
                     container_dir, cpu_shares, memory, memory_swap, user)
    pid = linux.clone(contain, flags, contain_args)

    # This is the parent, pid contains the PID of the forked process
    # wait for the forked child, fetch the exit status
    _, status = os.waitpid(pid, 0)
    print('{} exited with status {}'.format(pid, status))


if __name__ == '__main__':
    cli()
