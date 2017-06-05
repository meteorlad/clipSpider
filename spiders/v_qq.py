# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import string
import time
import re
import requests
import xmltodict
import json
from service.logger import Logger
from scrapy.exceptions import CloseSpider
from service.database import get_database
import service.utils
import os
import subprocess
import service.mail
import execjs
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('v.qq')


class VQqSpider(scrapy.Spider):
    name = "v.qq"
    allowed_domains = ["v.qq.com"]
    callbacked = False
    video_id = None
    times = 0

    # start_urls = (
    #     'http://www.v.qq.com/',
    # )
    # http://s.video.qq.com/loadplaylist?type=5&id=u3z9dt8bfxcgrr6&plname=qq
    # ad http://livew.l.qq.com/livemsg?pf=H5&ad_type=WL&pf_ex=mac&url=http%3A%2F%2Fv.qq.com%2Fcover%2Fu%2Fu3z9dt8bfxcgrr6.html%3Fvid%3Dd0019xtp6tj&ty=web&plugin=1.0.0&v=%24V2.0Build8588%24&coverid=u3z9dt8bfxcgrr6&vid=d0019xtp6tj&vptag=&pu=0&adaptor=1&dtype=1&live=0&_time_random=1471573031523&chid=0&low_login=1&_=1471573028020
    # playinfo http://h5vv.video.qq.com/getinfo?callback=jQuery191026505530742794825_1471573028021&vid=d0019xtp6tj&platform=10901&otype=json&ehost=v.qq.com&defn=auto&low_login=1&_=1471573028022

    def __init__(self, url, uuid, upload_url, callback, platform=11001, check_video_url=None, live_callback=None, *args,
                 **kwargs):
        super(VQqSpider, self).__init__(*args, **kwargs)

        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.uuid = uuid
        self.upload_url = upload_url
        self.callback = callback
        self.check_video_url = check_video_url
        self.live_callback = live_callback
        # initialize db
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))
        self.platform = platform
        self.start_urls.append(url)
        self.cookie = self.db.get_cookies('v.qq')
        if not self.cookie:
            self.cookie = self.config.get('Preference', 'v_qq_vip_cookie')

    def parse(self, response):
        print 'parsePlayurl', response.url

        if 'mp.weixin.qq.com' in self.start_urls[0]:
            self.start_urls[0] = response.selector.xpath('//iframe[@class="video_iframe"]/@data-src').extract()[0]
        elif 'live.qq.com' in self.start_urls[0]:
            return self.live(response)

        rel_url = self.start_urls[0]
        links = response.selector.xpath('//link[@rel="canonical"]/@href').extract()
        if 'y.qq.com' not in self.start_urls[0] and links and len(links) > 0:
            rel_url = links[0].strip()
        print 'rel_url', rel_url
        try:
            self.video_id = self.match_id(rel_url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        print 'video_id', self.video_id

        if self.check_db():
            return

        self.parse_info(rel_url)
        if self.callbacked:
            return

        try:
            if self.you_get():
                return
        except ValueError, e:
            print e

    def live(self, response):
        command = ['you-get', '-u', self.start_urls[0]]
        print command
        stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'stdout', stdout, 'stderr', stderr

        logger.info('[live you-get]' + '[uuid]' + self.uuid)
        m = re.compile(r'Real\s*URLs:\s*(?P<url>\S+)').search(stdout)
        if m:
            live_url = m.group('url')
            title = response.selector.xpath('//title/text()').extract()[0]
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'直播',
                "cover": '',
                "title": title,
                "live_url": live_url
            }
            print 'create live:', live_url
            self.callbacked = service.utils.callback_result(self.live_callback, data)

    def you_get(self):
        command = ['you-get', '--json', self.start_urls[0]]
        print command
        stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'stdout', stdout, 'stderr', stderr
        if len(stdout) < 2:
            return False

        logger.info('[you-get]' + '[uuid]' + self.uuid)
        video = json.loads(stdout)
        if 'streams' not in video:
            return False
        title = video['title']
        srcs = []
        for key in video['streams'].keys():
            print key
            if 'src' in video['streams'][key]:
                srcs = video['streams'][key]['src']
                print srcs
                break
        concatfile = 'cache/' + self.uuid + '.txt'
        mp4file = 'cache/' + self.uuid + '.mp4'
        for idx, src in enumerate(srcs):
            src_path = 'cache/' + self.uuid + '_' + str(idx) + '.mp4'
            _, success = service.utils.download_file(src, src_path)
            if not success:
                return False
            open(concatfile, 'a+').write('file ' + string.replace(src_path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(mp4file, concatfile)
        print '[merged video duration]', length
        if length == 0:
            return False
        filesize = os.path.getsize(mp4file)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        print 'uploadResult:', uploadResult
        if not uploadResult:
            return False

        logger.warn('[uploadVideo]' + '[uuid]' + self.uuid)

        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": '',
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': self.video_id,
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': '',
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('finished')

    def check_db(self):
        result = service.utils.search_video(video_id=self.video_id, page_url=self.start_urls[0],
                                            check_url=self.check_video_url)

        if result:
            print 'db note url:', self.start_urls[0]
            logger.warn('[db note url]' + self.start_urls[0] + '[uuid]' + self.uuid)
            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": result[2],
                "play_id": result[0],
                "size": result[3],
                "cover": '',
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

    def parse_info(self, url):
        print 'parseInfo', url

        getinfo_url = 'https://vv.video.qq.com/getinfo?&vid=%s&guid=11b723226ba84692eb159c32b62bfdf4&platform=10901&sdtfrom=v1010&newplatform=10901' % self.video_id
        ctx = execjs.compile(open('service/qq.js').read())
        vf = ctx.call('xx', '10901', self.video_id, 'v1010', '1', ctx.call('getTimeStampStr', 10))
        print vf
        getinfo_url += '&_qv_rmt=%s&_qv_rmt2=%s' % (vf['u1'], vf['u2'])
        print getinfo_url
        resp = requests.get(getinfo_url, headers={'Cookie': self.cookie})
        # resp = requests.get(getinfo_url, headers={'Cookie': self.config.get('Preference', 'v_qq_vip_cookie')})
        print resp.content
        info = xmltodict.parse(resp.content)
        if 'vl' not in info['root']:
            raise CloseSpider('video not found')

        video = info['root']['vl']['vi']
        part = video['cl']['fc']
        if int(part) > 1:
            self.multipart_video(video)
            return

        vkey = video['fvkey']
        name = video['fn']
        filesize = video['fs']
        length = video['td']
        title = video['ti']
        base_url = video['ul']['ui'][0]['url']

        # url = base_url + name
        # real_url = string.replace(url, '.mp4', '.1.mp4') + "?vkey=" + vkey
        real_url = base_url + name + "?sdtfrom=v1010&guid=11b723226ba84692eb159c32b62bfdf4&vkey=" + vkey
        if part == 1 or requests.head(real_url).status_code > 302:
            real_url = string.replace(real_url, '.mp4', '.1.mp4')

        print 'video real url:', real_url, filesize, length, title

        file_path = 'cache/%s.mp4' % self.uuid
        filesize, success = service.utils.download_file(real_url, file_path)
        if not success:
            raise CloseSpider("download video failed")

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj

        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            logger.error('upload video error', real_url)
            raise CloseSpider("upload oss failed")

        print 'filesize', filesize
        # callback
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": '',
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': self.video_id,
            'author': 'v.qq',
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': real_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)

    def multipart_video(self, video):
        parts = video['cl']['fc']
        print 'multipart video', parts
        vkey = video['fvkey']
        name = video['fn']
        # video_format = string.replace(string.split(name, '.')[1], 'p', '10')
        video_format = string.split(video['cl']['ci'][0]['keyid'], '.')[1]
        title = video['ti']
        base_url = video['ul']['ui'][0]['url']
        url = base_url + name
        concatfile = 'cache/' + self.uuid + '.txt'
        mp4file = 'cache/%s.mp4' % self.uuid
        for idx in range(1, int(parts) + 1):
            if idx == 1:
                real_url = string.replace(url, '.mp4', '.1.mp4') + "?sdtfrom=v1010&guid=11b723226ba84692eb159c32b62bfdf4&vkey=" + vkey
            else:
                filename = string.replace(name, '.mp4', '.%s.mp4' % idx)
                getkey_url = 'http://h5vv.video.qq.com/getkey?guid=11b723226ba84692eb159c32b62bfdf4&vid=%s&format=%s&filename=%s&platform=10901' % (
                    self.video_id, video_format, filename)
                ctx = execjs.compile(open('service/qq.js').read())
                vf = ctx.call('xx', '10901', self.video_id, 'v1010', '1', ctx.call('getTimeStampStr', 10))
                print vf
                getkey_url += '&_qv_rmt=%s&_qv_rmt2=%s' % (vf['u1'], vf['u2'])
                print 'getkey_url', getkey_url
                resp = requests.get(getkey_url, headers={'Cookie': self.cookie})
                # resp = requests.get(getkey_url, headers={'Cookie': self.config.get('Preference', 'v_qq_vip_cookie')})
                info = xmltodict.parse(resp.content)
                print info
                key = info['root']['key']
                real_url = base_url + filename + "?sdtfrom=v1010&guid=11b723226ba84692eb159c32b62bfdf4&vkey=" + key
                print 'real url', real_url

            path = 'cache/%s_%s.mp4' % (self.uuid, idx)
            filesize, success = service.utils.download_file(real_url, path)
            if not success:
                raise CloseSpider("download video failed")
            open(concatfile, 'a+').write('file ' + string.replace(path, 'cache/', '') + "\n")

        length = service.utils.mergeVideo(mp4file, concatfile)
        filesize = os.path.getsize(mp4file)
        if length > 1 and filesize > 1024:
            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            result = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
            if not result:
                raise CloseSpider("upload oss failed")

            data = {
                "video_id": self.uuid,
                "state": 1,
                "message": u'成功',
                "length": length,
                "play_id": self.uuid,
                "size": filesize,
                "cover": '',
                "title": title
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

            video_data = {
                'title': title,
                'video_id': self.video_id,
                'author': 'v.qq',
                'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                'page_url': self.start_urls[0],
                'video_length': length,
                'video_size': filesize,
                'video_url': '',
                'easub_uuid': self.uuid
            }
            self.db.save_video(video_data)

    def match_id(self, url):
        # http://v.qq.com/cover/u/u3z9dt8bfxcgrr6/q0019x0j2sj.html
        # http://v.qq.com/cover/u/u3z9dt8bfxcgrr6.html?vid=a0020wju32y
        # http://v.qq.com/x/page/u0309isefd8.html
        # http://v.qq.com/boke/gplay/bd0b7eb38c6c019ff9a10ae0a7d3f9b1_9zw000001wj8je2_19_z030556tt9c.html
        # http://v.qq.com/topic/2015/2016guoman.html#c0173rqy2cy

        regx = r"https?://.*\.qq\.com/.*(iframe|cover|page|topic|boke/gplay|v)/(.*(/|_|#|vid=))?(?P<video_id>\w{11})"
        m = re.compile(regx).match(url)
        assert m
        return m.group('video_id')

    def closed(self, reason):
        print '[closed]', reason
        if not self.callbacked:
            data = {
                "video_id": self.uuid,
                "state": 0,
                "message": reason,
                "length": 0,
                "play_id": '',
                "cover": '',
                "title": ''
            }
            service.utils.callback_result(self.callback, data=data)
            service.mail.send_mail('spider failed:' + self.name, self.uuid + ' ' + reason)
        logger.info('[closed reason]' + reason + "[uuid]" + self.uuid)
        if self.video_id:
            # subprocess.Popen('rm -rf ' + 'cache/' + self.uuid + '*', shell=True)
            service.utils.remove_all_files('cache/', self.uuid)
