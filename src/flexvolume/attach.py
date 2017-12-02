import os
import stat
import subprocess

from etcd3autodiscover import Etcd3Autodiscover
from decimal import Decimal

try:
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, 'wb')

import click
import json

from vcloud import client as Client, disk as Disk, vapp as VApp
from vcloud.utils import disk_partitions, wait_for_connected_disk
from .cli import cli, error, info

@cli.command(short_help='attach the volume to the node')
@click.argument('params')
@click.argument('nodename')
@click.pass_context
def attach(ctx,
           params,
           nodename):
    params = json.loads(params)
    config = Client.ctx.config
    try:
        is_logged_in = Client.login()
        if is_logged_in == False:
            raise Exception("Could not login to vCloud Director")
        volume = params['volumeName']
        disk_storage = params['storage'] if 'storage' in params else config['default_storage']
        disk_bus_type = int(params['busType']) if 'busType' in params else None
        disk_bus_sub_type = params['busSubType'] if 'busSubType' in params else None

        disk_urn, attached_vm = Disk.find_disk(
                Disk.get_disks(Client.ctx),
                volume
        )
        if disk_urn is None:
            disk_urn = Disk.create_disk(
                    Client.ctx,
                    volume,
                    params['size'],
                    disk_storage,
                    bus_type=disk_bus_type,
                    bus_sub_type=disk_bus_sub_type
            )
            if disk_urn == "":
                raise Exception(
                        ("Could not create volume '%s'") % (volume)
                )

        volume_symlink = ("/dev/block/%s") % (disk_urn)

        if attached_vm:
            # Disk is in attached state
            vm = VApp.find_vm_in_vapp(
                    Client.ctx,
                    vm_id=attached_vm)
            # Check if attached to current node
            if len(vm) > 0:
                vm_name = vm[0]['vm_name']

                if vm_name != nodename:
                    is_disk_detached = Disk.detach_disk_b(
                            Client.ctx,
                            vm_name,
                            volume)
                    if is_disk_detached == False:
                        raise Exception(
                                ("Could not detach volume '%s' from '%s'") % (volume, vm_name)
                        )
                    attached_vm = None
            else:
                raise Exception(
                        ("Could not find attached VM '%s'. Does the VM exist?") % (attached_vm)
                )

        if attached_vm is None:
            etcd = Etcd3Autodiscover(host=config['etcd']['host'],
                                     ca_cert=config['etcd']['ca_cert'],
                                     cert_key=config['etcd']['key'],
                                     cert_cert=config['etcd']['cert'],
                                     timeout=config['etcd']['timeout'])
            client = etcd.connect()
            if client is None:
                raise Exception(
                        ("Could not connect to etcd server '%s'") % (etcd.errstr())
                )
            lock_name = ("vcloud/%s/disk/attach") % (nodename)
            lock_ttl = 120
            with client.lock(lock_name, ttl=lock_ttl) as lock:
                n = 0
                absolute = 10
                while lock.is_acquired() == False and n < 6:
                    timeout = round(Decimal(4 * 1.29 ** n))
                    absolute += timeout
                    n += 1
                    lock.acquire(timeout=timeout)

                if lock.is_acquired() == False:
                    raise Exception(
                            ("Could not acquire lock after %0.fs. Giving up") % (absolute)
                    )
                lock.refresh()
                is_disk_attached = Disk.attach_disk(
                        Client.ctx,
                        nodename,
                        volume
                )
                if is_disk_attached == False:
                    raise Exception(
                            ("Could not attach volume '%s' to node '%s'") % (volume, nodename)
                    )
                is_disk_connected = wait_for_connected_disk(60)
                if len(is_disk_connected) == 0:
                    raise Exception(
                            ("Timed out while waiting for volume '%s' to attach to node '%s'") % \
                                    (volume, nodename)
                    )
                # Make sure task is completed
                if hasattr(is_disk_attached, 'id'):
                    Client.ctx.vca.block_until_completed(is_disk_attached)

                device_name, device_status = is_disk_connected
                if os.path.lexists(volume_symlink) == False:
                    os.symlink(device_name, volume_symlink)
            
            lock.release()
        else:
            if os.path.lexists(volume_symlink):
                device_name = os.readlink(volume_symlink)
                try:
                    mode = os.stat(device_name).st_mode
                    assert stat.S_ISBLK(mode) == True
                except OSError:
                    raise Exception(
                            ("Device '%s' does not exist on node '%s'") % (device_name, nodename)
                    )
                except AssertionError:
                    raise Exception(
                            ("Device '%s' exists on node '%s' but is not a block device") % \
                                    (device_name, nodename)
                    )
            else:
                import inspect
                raise Exception(
                        ("Fatal error on line %d. This should never happen") % (inspect.currentframe().f_lineno)
                )

        partitions = disk_partitions(device_name.split('/')[-1])
        if len(partitions) == 0:
            try:
                # See: http://man7.org/linux/man-pages/man8/sfdisk.8.html
                cmd_create_partition = ("echo -n ',,83;' | sfdisk %s") % (device_name)
                subprocess.check_call(
                        cmd_create_partition,
                        shell=True,
                        stdout=DEVNULL,
                        stderr=DEVNULL
                )
                partition = ("%s%d") % (
                        device_name,
                        1
                )
            except subprocess.CalledProcesError:
                raise Exception(
                        ("Could not create partition on device '%s'") % (device_name)
                )
        else:
            partitions.sort()
            partition = ("/%s/%s") % (
                    device_name.split('/')[1],
                    partitions[0]
            )

        success = {
            "status": "Success",
            "device": "%s" % partition
        }
        info(success)
    except Exception as e:
        failure = {
            "status": "Failure",
            "message": "%s" % e
        }
        error(failure)
    finally:
        Client.logout()

@cli.command(short_help='wait for the mount device to be attached on the remote node')
@click.argument('mountdev')
@click.argument('params')
@click.pass_context
def waitforattach(ctx,
                  mountdev,
                  params):
    params = json.loads(params)
    try:
        is_logged_in = Client.login()
        if is_logged_in == False:
            raise Exception("Could not login to vCloud Director")
        volume = params['volumeName']
        disk_urn, attached_vm = Disk.find_disk(
                Disk.get_disks(Client.ctx),
                volume
        )
        if disk_urn is None:
            raise Exception(
                    ("Volume '%s' does not exist") % (mountdev)
            )

        volume_symlink = ("/dev/block/%s") % (disk_urn)

        if os.path.lexists(volume_symlink):
            device_name = os.readlink(volume_symlink)
            try:
                mode = os.stat(device_name).st_mode
                assert stat.S_ISBLK(mode) == True
            except OSError:
                raise Exception(
                        ("Device '%s' does not exist") % (device_name)
                )
            except AssertionError:
                raise Exception(
                        ("Device '%s' exists but is not a block device") % \
                                (device_name)
                )
        partitions = disk_partitions(device_name.split('/')[-1])
        attached = False
        for part in partitions:
            if part == mountdev.split('/')[-1]:
                attached = True
                break
        if attached:
            success = {
                "status": "Success",
                "device": "%s" % mountdev
            }
            info(success)
        else:
            raise Exception(
                    ("Mount device '%s' is not attached on the remote node") % \
                            (mountdev)
            )
    except Exception as e:
        failure = {
            "status": "Failure",
            "message": "%s" % e
        }
        error(failure)
    finally:
        Client.logout()

@cli.command(short_help='check the volume is attached on the node')
@click.argument('params')
@click.argument('nodename')
@click.pass_context
def isattached(ctx,
               params,
               nodename):
    attached = False
    params = json.loads(params)
    try:
        is_logged_in = Client.login()
        if is_logged_in == False:
            raise Exception("Could not login to vCloud Director")

        vm = VApp.find_vm_in_vapp(Client.ctx, vm_name=nodename)
        if len(vm) > 0:
            vm = vm[0]['vm']
            volume = params['volumeName']
            disks = Disk.get_disks(Client.ctx)
            for disk in disks:
                if disk['name'] == volume \
                        and disk['attached_vm'] == vm:
                    attached = True
                    break
            success = {
                "status": "Success",
                "attached": attached
            }
            info(success)
        else:
            raise Exception(
                    ("Could not find node '%s'") % (nodename)
            )
    except Exception as e:
        failure = {
            "status": "Failure",
            "message": "%s" % e
        }
        error(failure)
    finally:
        Client.logout()
