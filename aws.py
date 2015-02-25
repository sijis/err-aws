from errbot import BotPlugin, botcmd
from optparse import OptionParser

import logging
import socket
import time

log = logging.getLogger(name='errbot.plugins.AWS')

try:
    from libcloud.compute.types import Provider, NodeState
    from libcloud.compute.providers import get_driver
    from libcloud.compute.base import NodeImage
    from libcloud.compute.drivers.ec2 import EC2SubnetAssociation
except ImportError:
    log.error("Please install 'apache-libcloud' python package")

try:
    import feedparser
except ImportError:
    log.error("Please install 'feedparser' python package")


class AWS(BotPlugin):

    def get_configuration_template(self):
        """ configuration entries """
        config = {
            'access_id': None,
            'secret_key': None,
            'ami': None,
            'keypair': None,
            'subnet_id': None,
            'route_table_id': None,
            'volume_size': 1,
            'instance_type': None,
            'datacenter': None,
            'puppet': False,
        }
        return config

    def _connect(self):
        """ connection to aws """
        access_id = self.config['access_id']
        secret_key = self.config['secret_key']
        datacenter = self.config['datacenter']

        cls = get_driver(datacenter)
        driver = cls(access_id, secret_key)
        return driver

    def _find_instance_by_name(self, name):
        driver = self._connect()
        for instance in driver.list_nodes():
            if instance.name == name:
                return instance

    def _find_instance_by_id(self, id):
        driver = self._connect()
        for instance in driver.list_nodes():
            if instance.id == id:
                return instance

    def _basic_instance_details(self, name):
        instance = self._find_instance_by_name(name)

        if instance is not None:
            details = {
                'id': instance.id,
                'status': NodeState.tostring(instance.state),
                'ip-private': instance.private_ips,
                'ip-public': instance.public_ips,
                'security_groups': instance.extra['groups'],
                'keypair': instance.extra['key_name'],
                'instance_type': instance.extra['instance_type'],
            }
        else:
            details = {'error': 'instance named {0} not found.'.format(name)}

        return details

    @botcmd(split_args_with=' ')
    def aws_info(self, msg, args):
        ''' get details of a virtual machine
            options: name
            example:
            !aws info log1
        '''
        vmname = args.pop(0)
        details = self._basic_instance_details(vmname)
        self.send(msg.frm,
                  '{0}: {1}'.format(vmname, details),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)

    @botcmd
    def aws_reboot(self, msg, args):
        ''' reboot a virtual machine
            options:
                vm (name): name of virtual machine
            example:
            !aws reboot log1
        '''
        vm = self._find_instance_by_name(args)
        result = vm.reboot()
        response = ''
        if result:
            response = 'Successfully sent request to reboot.'
        else:
            response = 'Unable to complete request.'

        self.send(msg.frm,
                  '{0}: {1}'.format(vm.name, response),
                  message_type=msg.type)

    @botcmd
    def aws_terminate(self, msg, args):
        ''' terminate/destroy a virtual machine
            options:
                vm (name): name of instance
            example:
            !aws terminate log1
        '''
        vm = self._find_instance_by_name(args)
        result = vm.destroy()
        response = ''
        if result:
            response = 'Successfully sent request to terminate instance.'
        else:
            response = 'Unable to complete request.'

        self.send(msg.frm,
                  '{0}: {1}'.format(vm.name, response),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)

    @botcmd(split_args_with=' ')
    def aws_create(self, msg, args):
        ''' create a virtual machine from ami template
            options:
                ami (str): template ami to use
                size (int): disk size of instance in GBs
                tags (str): key=val tags
                subnet_id (str): vpc subnet
                route_table_id (str): vpc subnet's routing table
                keypair (str): key pair to use
                instance_type (str): ami instance type
                puppet (bool): run puppet after provisioning
            example:
            !aws create --ami=i-12321 --size=20 --tags="key1=val1,key2=val2" --keypair=my-key --instance_type=t2.medium --puppet app-server1
        '''
        parser = OptionParser()
        parser.add_option("--ami", dest="ami", default=self.config['ami'])
        parser.add_option("--size", dest="size", type='int', default=15)
        parser.add_option("--subnet_id", dest="subnet_id",
                          default=self.config['subnet_id'])
        parser.add_option("--route_table_id", dest="route_table_id",
                          default=self.config['route_table_id'])
        parser.add_option("--instance_type", dest="instance_type",
                          default=self.config['instance_type'])
        parser.add_option("--tags", dest="tags")
        parser.add_option("--keypair", dest="keypair",
                          default=self.config['keypair'])
        parser.add_option("--puppet", action="store_false",
                          dest="puppet", default=self.config['puppet'])

        (t_options, t_args) = parser.parse_args(args)
        options = vars(t_options)

        vmname = t_args.pop(0)

        # setting up requirements
        network = EC2SubnetAssociation(
            id=options['subnet_id'],
            route_table_id=options['route_table_id'],
            subnet_id=options['subnet_id'],
            main=True
        )

        block_dev_mappings = [{'VirtualName': None,
                               'Ebs': {
                                   'VolumeSize': options['size'],
                                   'VolumeType': 'standard',
                                   'DeleteOnTermination': 'true'
                               },
                               'DeviceName': '/dev/sda'}]

        base_tags = {
            'Name': vmname,
            'team': 'systems',
        }

        if options['tags'] is not None:
            for t_tags in options['tags'].split(','):
                base_tags.update(dict([keys.split('=')]))

        driver = self._connect()

        # Setting up ami + instance type
        sizes = driver.list_sizes()
        size = [s for s in sizes if s.id == options['instance_type']][0]
        image = NodeImage(id=options['ami'], name=None, driver=driver)

        # using key-pair and group
        node = driver.create_node(name=vmname, image=image, size=size,
                                  ex_keyname=options['keypair'],
                                  # ex_securitygroup=SECURITY_GROUP_NAMES,
                                  ex_subnet=network,
                                  ex_blockdevicemappings=block_dev_mappings,
                                  ex_metadata=base_tags)

        self.send(msg.frm,
                  '{0}: [1/3] Creating instance'.format(vmname),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)
        # todo: actually query state of instance
        # time.sleep(30)
        self.send(msg.frm,
                  '{0}: [2/3] Running post setup'.format(vmname),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)

        if options['puppet']:
            # ready for puppet... let's go!
            self.send(msg.frm,
                      '{0}: Running puppet [disabled]'.format(vmname),
                      message_type=msg.type,
                      in_reply_to=msg,
                      groupchat_nick_reply=True)

        self.send(msg.frm,
                  '{0}: [3/3] Request completed'.format(vmname),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)
        self.send(msg.frm,
                  '{0}: {1}'.format(vmname,
                                    self._basic_instance_details(vmname)),
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)

    def _parse_status_results(self, results):
        response = []
        count = 1 
        for result in results:
            response.append('{0}. ({1}) {2} - {3}'.format(
                count,
                result['published'],
                result['title'],
                result['summary'],
                )
            )
            count = count + 1 
        return ' '.join(response)

    @botcmd(split_args_with=' ')
    def aws_status(self, msg, args):
        ''' Get rss feed from aws status page
            options:
                service (str): services (ec2 [default], elb, route53, s3, vpc, etc)
                timeout (int): rss feed timeout value
                region  (str): regions (us-east-1 [default], us-west-1, ap-southeast-1, etc)
                entries (int): last number of entries from feed (default: 1)
            example:
            !aws status --service=route53 --region=us-west-2
        '''
        parser = OptionParser()
        parser.add_option("--service", dest="service", default='ec2')
        parser.add_option("--timeout", dest="timeout", type='int', default=5)
        parser.add_option("--region", dest="region", default='us-east-1')
        parser.add_option("--entries", dest="entries", type='int', default=1)

        (t_options, t_args) = parser.parse_args(args)
        options = vars(t_options)

        # set timeout
        timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(options['timeout'])

        aws_url = 'http://status.aws.amazon.com/rss/{}-{}.rss'.format(
            options['service'],
            options['region']
        )
        log.debug('Getting feed from {}'.format(aws_url))

        feeds = feedparser.parse(aws_url)

        if len(feeds['entries']) < 1:
            self.send(msg.frm,
                      'No entries found.',
                      message_type=msg.type,
                      in_reply_to=msg,
                      groupchat_nick_reply=True)
            return

        content = self._parse_status_results(feeds['entries'][:options['entries']])
        content += ' Source: {}'.format(aws_url)

        self.send(msg.frm,
                  content,
                  message_type=msg.type,
                  in_reply_to=msg,
                  groupchat_nick_reply=True)
