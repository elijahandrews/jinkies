#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Jinkies is a command line jenkins program.

Usage:
    jinkies list (jobs|views)
    jinkies show <view>
    jinkies build <job>
    jinkies view <job>
    jinkies --config

Options:
    -h --help       Show this help.
    --version       Show version and exit.
    --config        Show config and exit.
"""

import sys
import os
import re
import cookielib
import requests
import docopt
import time
from pprint import pformat

url_help = """Please set JENKINS_URL to the url to your jenkins instance.

If your jenkins is behind a login, you can first go to:
    https://jenkins/user/<yourname>/configure

And get a token by clicking "Show API Token", and then use a URL like:
    https://<yourname>:<yourtoken>@jenkins/
"""

URL=""

white,black,red,green,yellow,blue,purple = range(89,96)
def color(string, color=green, bold=False, underline=False):
    """Usage: color("foo", red, bold=True)"""
    s = '01;' if bold else '04;' if underline else ''
    return '\033[%s%sm' % (s, color) + str(string) + '\033[0m'

# boo
spre = re.compile(r'<span style="color: #(?P<color>[0-9A-F]{6});">(?P<txt>.*?)</span>')
are = re.compile(r'<a href=.*?>(?P<txt>.*?)</a>')
spnre = re.compile(r'<span.*?>(?P<txt>.*?)</span>')
bre = re.compile(r'<b>(?P<txt>.*?)</b>')

colmap = {
    '00CDCD': lambda s: color(s, color=blue, bold=True),
    'CDCD00': lambda s: color(s, color=yellow, bold=True),
    '00CD00': lambda s: color(s, color=green, bold=True),
    'CD0000': lambda s: color(s, color=red, underline=False),
    'link': lambda s: color(s, color=red, underline=True),
    'bold': lambda s: color(s, color=white, bold=True),
    '': lambda s: s,
}

def colorize(text):
    def rep(default):
        def inner(group):
            d = group.groupdict()
            color = d.get('color', default)
            txt = d.get('txt', '')
            return colmap[color](txt)
        return inner
    s = text
    s, _ = spre.subn(rep(''), s)
    s, _ = spnre.subn(rep(''), s)
    s, _ = are.subn(rep('link'), s)
    s, _ = bre.subn(rep('bold'), s)
    s = s.replace('&gt;', '>')
    s = s.replace('&lt;', '<').lstrip()
    return s

def main():
    global URL
    args = docopt.docopt(__doc__, version="1.0")
    if os.getenv("JENKINS_URL"):
        URL = os.getenv("JENKINS_URL")
    if not URL:
        print url_help
        return
    if args['--config']:
        print "URL: %s" % (URL)
        return
    if args['list']:
        return cmd_list(args)
    elif args['show']:
        return cmd_show(args)
    elif args['build']:
        return cmd_build(args)
    elif args['view']:
        return cmd_view(args)

def print_job(job):
    print job['name']

def print_response_err(resp):
    print "Error: %s" % (resp)
    print resp.text

def cmd_list(args):
    url = "%s/api/json" % URL
    resp = requests.get(url)
    if not resp.ok:
        print_response_err(resp)
        return
    doc = resp.json()
    if args['jobs']:
        for job in doc['jobs']:
            print_job(job)
    elif args['views']:
        for view in doc['views']:
            print "%s: %s" % (view['name'], view['url'])

def cmd_show(args):
    url = "%s/view/%s/api/json" % (URL, args['<view>'])
    resp = requests.get(url)
    if not resp.ok:
        print_response_err(resp)
        return
    doc = resp.json()
    for job in doc['jobs']:
        print_job(job)

def cmd_view(args):
    job = args['<job>']
    url = "%s/job/%s/api/json" % (URL, job)
    resp = requests.get(url)
    if not resp.ok:
        print_response_err(resp)
        return
    doc = resp.json()
    # if there is a queued job, lets wait for it to start
    if doc['inQueue']:
        next = doc['nextBuildNumber']
        watch(URL, job, next)
        return

    previous = doc['lastBuild']
    previousFinished = doc['lastCompletedBuild']

    if previous['number'] == previousFinished['number']:
        print "Showing previous build %d" % previous['number']
        print '\n'.join(get_console(job, previous['number']))
        print ""
        print "Showed previous build:"
        print "%s/job/%s/%s" % (URL, job, previous['number'])
        return

    watch(URL, job, previous['number'])


def watch(URL, job, build):
    """Watch console output for a job.  In the event that it hasn't begun yet
    (eg. it is queued), wait for it to start and then watch the output."""
    console = lambda: get_console(job, build)

    first = True
    firstWait = True
    url = "%s/job/%s/%s/api/json" % (URL, job, build)
    cp = 0
    failures = 0
    waits = 0
    while 1:
        resp = requests.get(url)
        if not resp.ok and first:
            r2 = requests.get("%s/job/%s/api/json" % (URL, job))
            waits += 1
            if not r2.ok:
                print "Failure loading job for %s" % (job)
                print r2.data
                return
            d = r2.json()
            if d['inQueue']:
                if firstWait:
                    sys.stdout.write('Waiting in job queue .')
                    firstWait = False
                else:
                    sys.stdout.write('.')
                sys.stdout.flush()
                time.sleep(2.5)
            elif not resp.ok:
                failures += 1
                if failures > 5:
                    print "Failure loading job for %s" % (job)
                    return
            continue
        if first and (failures or waits):
            print ""
        doc = resp.json()
        if first:
            print "Started build #%d, ETA %.1fs" % (build, doc['estimatedDuration']/1000.0)
            first = False
        cons = console()
        if len(cons) > cp:
            print "\n".join(cons[cp:]),
            cp = len(cons)-1
        if not doc['building']:
            print doc['result']
            return
        time.sleep(1.5)

def get_console(job, build):
    resp = requests.get("%s/job/%s/%s/logText/progressiveHtml" % (URL, job, build))
    if not resp.ok:
        return []
    text = colorize(resp.text)
    lines = [l.lstrip() for l in text.split("\n")]
    return lines

def cmd_build(args):
    # first, fetch the job to figure out what the next build number is
    # this also lets us bail out if the job is invalid
    job = args['<job>']
    url = "%s/job/%s/api/json" % (URL, job)
    resp = requests.get(url)
    if not resp.ok:
        print_response_err(resp)
        return
    doc = resp.json()
    build = doc['nextBuildNumber']

    # now lets start the build job
    url = "%s/job/%s/build?delay=0sec" % (URL, job)
    resp = requests.post(url)
    if not resp.ok:
        print "Error starting build:"
        print_response_err(resp)
        return

    watch(URL, job, build)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
