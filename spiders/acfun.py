# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
import scrapy
import ConfigParser
import json
import string
import os
import subprocess
import time
from service.logger import Logger
from service.database import get_database
import service.utils
import re
from scrapy.exceptions import CloseSpider
import service.acfun
import youtube_dl
from scrapy.selector import Selector
import requests
import urllib
import execjs
import service.mail
from scrapy.http import Request, FormRequest
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('acfun')


class AcfunSpider(scrapy.Spider):
    name = "acfun"
    allowed_domains = ["acfun.tv", "acfun.cn"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(AcfunSpider, self).__init__(*args, **kwargs)

        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.uuid = uuid
        self.upload_url = upload_url
        self.callback = callback
        self.check_video_url = check_video_url
        # initialize db
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))
        self.start_urls.append(url)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.87 Safari/537.36',
            'Host': 'www.acfun.cn',
            'Cookie': 'uuid=c9da88c1238512ed961a461e8bde57dd; sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%221593df8a601f7-03485fb6b71f09-1d316f50-1fa400-1593df8a6022a3%22%7D; tma=191396026.46866448.1487671944652.1488970326344.1489398920581.4; tmd=22.191396026.46866448.1487671944652.; bfd_g=bffd842b2b484332000047ad00b6c72c55f7f17c; Hm_lvt_bc75b9260fe72ee13356c664daa5568c=1489547693; Hm_lpvt_bc75b9260fe72ee13356c664daa5568c=1489547822; Hm_lvt_bc75b9260fe72ee13356c664daa5568c=1489547693; Hm_lpvt_bc75b9260fe72ee13356c664daa5568c=1490673699; clientlanguage=zh_CN',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0',
            'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4',
            'Accept-Encoding': 'gzip, deflate, sdch',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }

    def start_requests(self):
        return [Request(self.start_urls[0], callback=self.parse, headers=self.headers)]

    def parse(self, response):
        print response.url

        try:
            self.video_id = self.match_id(response.url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        title = response.selector.xpath("//title/text()").extract()[0].strip()
        print 'title', title
        try:
            self.get_video(response)
            if self.callbacked:
                return
        except Exception, e:
            print e

        if self.you_get():
            return

        source_type = re.search(r'source_type\":\"(?P<source_type>\w+)\"', response.body).group("source_type")
        print 'source_type', source_type
        if source_type != 'youku':
            return

        self.video_id = re.search(r'source_id\":\"(?P<source_id>[a-zA-Z0-9-=]+)\"', response.body).group("source_id")
        self.start_urls[0] = "http://v.youku.com/v_show/id_%s.html" % self.video_id
        print 'youku url', self.start_urls[0]
        if self.check_db():
            return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': '22/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            # 'noplaylist': True,
            # 'logger': MyLogger(),
        }
        print ydl_opts
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(self.start_urls)

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
                "cover": result[4],
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

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

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            # id = info['playlist_id']
            concatfile = 'cache/' + self.uuid + '.txt'
            open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")
            if info['playlist_index'] == info['n_entries']:
                mp4file = 'cache/' + self.uuid + '.mp4'
                length = service.utils.mergeVideo(mp4file, concatfile)
                print '[merged video duration]', length
                if length == 0:
                    raise CloseSpider('merge video failed')
                filesize = os.path.getsize(mp4file)
                endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
                print endpoint, backet, obj
                uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
                subprocess.Popen('rm -rf ' + 'cache/' + self.uuid + '*', shell=True)
                print 'uploadResult:', uploadResult
                if not uploadResult:
                    raise CloseSpider('upload oss failed')

                if 'thumbnail' in info:
                    cover = service.utils.get_clip_cover_url(info['thumbnail'], self.uuid)
                else:
                    cover = ''
                data = {
                    "video_id": self.uuid,
                    "state": 1,
                    "message": u'成功',
                    "length": length,
                    "play_id": self.uuid,
                    "size": filesize,
                    "cover": cover,
                    "title": info['fulltitle']
                }
                self.callbacked = service.utils.callback_result(self.callback, data=data)
                logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)
                video_data = {
                    'title': info['fulltitle'],
                    'video_id': self.video_id,
                    'author': info['extractor'],
                    'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'page_url': self.start_urls[0],
                    'video_length': length,
                    'video_size': filesize,
                    'video_url': '',
                    'easub_uuid': self.uuid,
                    'cover': cover
                }
                self.db.save_video(video_data)
                raise CloseSpider('finished')

        if d['status'] == 'error':
            print 'error', d['filename']
            raise CloseSpider('download failed')

    def get_video(self, response):
        pageInfo = response.selector.xpath('//div[@id="pageInfo"]').extract()[0]
        selector = Selector(text=pageInfo)
        title = selector.xpath('//div/@data-title').extract()[0]
        vid = selector.xpath('//div/@data-vid').extract()[0]
        img = selector.xpath('//div/@data-pic').extract()[0]
        print title, vid, img

        youku_plays = "http://api.aixifan.com/plays/youku/" + vid
        result = requests.get(youku_plays, headers={"deviceType": str(2)}).json()
        print youku_plays, result
        if result["code"] != 200:
            return

        video_id = result['data']['sourceId']
        embsig = result['data']['embsig']
        # refer = response.url
        # players_custom = "https://api.youku.com/players/custom.json?type=h5&client_id=908a519d032263f8&video_id=%s&embsig=%s&refer=%s&callback=ykv.callbacks.cb_" % (video_id, embsig, refer)
        # print players_custom
        # custom_resp = requests.get(players_custom)
        # print custom_resp.content[18:-2]
        # custom = json.loads(custom_resp.content[18:-2])
        acfun_web = "http://www.acfun.cn/jsplayer_data?vid=%s&ct=86&ev=4&cid=908a519d032263f8&sign=%s&callback=ykv.callbacks.cb_" % (
        video_id, embsig)
        print acfun_web
        acfun_web_resp = requests.get(acfun_web)
        print acfun_web_resp.content[18:-1]
        resp = json.loads(acfun_web_resp.content[18:-1])
        ctx = execjs.compile(open('service/acfun.js').read())
        info_str = ctx.call('jie', 'm1uN9G6cKz0mooZM', ctx.call('decode64', resp['data']))
        print info_str
        info = json.loads(info_str)
        urls = []
        length = 0
        for stream in info['stream']:
            if 'segs' in stream:
                for seg in stream['segs']:
                    urls.append(seg['url'] + "&start=0")
                    length += seg['seconds']
                break
        print urls
        if len(urls) == 0:
            return

        mp4file = 'cache/' + self.uuid + '.mp4'
        # if len(urls) == 1:
        #     filesize, success = service.utils.download_file(urls[0], mp4file)
        #     if not success:
        #         raise CloseSpider(u"下载视频失败")
        # else:
        concatfile = 'cache/' + self.uuid + '.txt'
        for idx, url in enumerate(urls):
            path = 'cache/%s-%s.mp4' % (self.uuid, idx)
            filesize, success = service.utils.download_file(url, path)
            if not success:
                raise CloseSpider('download video failed')
            open(concatfile, 'a+').write('file ' + string.replace(path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(mp4file, concatfile)
        if length == 0:
            raise CloseSpider('merge video failed')

        filesize = os.path.getsize(mp4file)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        if not uploadResult:
            raise CloseSpider('upload oss failed')

        cover = service.utils.get_clip_cover_url(img, self.uuid)
        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": cover,
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
            'easub_uuid': self.uuid,
            'cover': cover
        }
        self.db.save_video(video_data)

    def match_id(self, url):
        regx = r'https?://(?:www\.)?acfun\.(tv|cn)/v/ac(?P<id>[0-9]+)'
        m = re.compile(regx).match(url)
        assert m
        return m.group('id')

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
            service.utils.remove_all_files('cache/', self.uuid)
