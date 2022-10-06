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
    """Create a container root by extracting an image into a new directory

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
    container_root = _get_container_path(container_id, container_dir, 'rootfs')

    assert os.path.exists(image_path), "unable to locate image %s" % image_name

    if not os.path.exists(container_root):
        os.makedirs(container_root)

    # Here, we need to change the mount type because `pivot_root`
    # needs two different type filesystem
    # TODO: Remove after adding overlay support
    linux.mount('tmpfs', container_root, 'tmpfs', 0, None)

    with tarfile.open(image_path) as t:
        # Fun fact: tar files may contain *nix devices! *facepalm*
        members = [m for m in t.getmembers()
                   if m.type not in (tarfile.CHRTYPE, tarfile.BLKTYPE)]
        t.extractall(container_root, members=members)

    return container_root


@click.group()
def cli():
    pass


def contain(command, image_name, image_dir, container_id, container_dir):

    new_root_path = create_container_root(image_name, image_dir, container_id, container_dir)

    linux.unshare(linux.CLONE_NEWNS)

    # (https://www.kernel.org/doc/Documentation/filesystems/sharedsubtree.txt)
    # Make / a private mount to avoid littering our host mount table.
    # Use `man mount_namespaces`
    # MS_PRIVATE: This mount is private; it does not have a peer group. Mount
    #             and unmount events do not propagate into or out of this mount.
    # MS_REC: We need to recursive does this.
    linux.mount(None, '/', None, linux.MS_PRIVATE | linux.MS_REC, None)

    # We should mount `proc`, `sys` and recursive mount `/dev`.
    # This is like the way as the command line.
    # We could use `cat /proc/filesystems` to see the virtual
    # filesystem
    linux.mount('proc', os.path.join(new_root_path, 'proc'), 'proc', 0, '')
    linux.mount('sysfs', os.path.join(new_root_path, 'sys'), 'sysfs', 0, '')
    linux.mount('tmpfs', os.path.join(new_root_path, 'dev'), 'tmpfs',
                linux.MS_NOSUID | linux.MS_STRICTATIME, 'mode=755')

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

    old_root_path = os.path.join(new_root_path, '.pivot_root')
    os.makedirs(old_root_path)
    linux.pivot_root(new_root_path, old_root_path)
    # After `pivot_root`, the working diretcory would be corrupted.
    # So we should use `os.chdir`to change the working directory
    os.chdir('/')

    # Now `old_root_path` should be changed to `./.pivot_root`
    old_root_path = './.pivot_root'
    linux.umount2(old_root_path, linux.MNT_DETACH)
    os.rmdir(old_root_path)

    os.execvp(command[0], command)


@cli.command(context_settings=dict(ignore_unknown_options=True,))
@click.option('--image-name', '-i', help='Image name', default='ubuntu')
@click.option('--image-dir', help='Images directory',
              default='/workshop/images')
@click.option('--container-dir', help='Containers directory',
              default='/workshop/containers')
@click.argument('Command', required=True, nargs=-1)
def run(image_name, image_dir, container_dir, command):
    container_id = str(uuid.uuid4())
    pid = os.fork()
    if pid == 0:
        # This is the child, we'll try to do some containment here
        try:
            contain(command, image_name, image_dir, container_id,
                    container_dir)
        except Exception:
            traceback.print_exc()
            os._exit(1)  # something went wrong in contain()

    # This is the parent, pid contains the PID of the forked process
    # wait for the forked child, fetch the exit status
    _, status = os.waitpid(pid, 0)
    print('{} exited with status {}'.format(pid, status))


if __name__ == '__main__':
    cli()
