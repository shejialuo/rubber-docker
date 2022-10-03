from __future__ import print_function

import click
import os
import traceback


@click.group()
def cli():
    pass


def contain(command):
    # NOTE: command is an array (the first element is path/file, and the entire
    #       array is exec's args)
    os.execvp(command[0], command)


@cli.command(context_settings=dict(ignore_unknown_options=True,))
@click.argument('Command', required=True, nargs=-1)
def run(command):
    pid = os.fork()
    if pid == 0:
        # This is the child, we'll try to do some containment here
        try:
            contain(command)
        except Exception:
            traceback.print_exc()
            os._exit(1)  # something went wrong in contain()

    # This is the parent, pid contains the PID of the forked process
    # wait for the forked child and fetch the exit status
    _, status = os.waitpid(pid, 0)
    print('{} exited with status {}'.format(pid, status))


if __name__ == '__main__':
    cli()
