# -*- coding: utf-8 -*-
import youtube_dl
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
import service.mail
import sys
import requests
import execjs
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('le')

class LeSpider(scrapy.Spider):
    name = "le"
    allowed_domains = ["le.com"]
    callbacked = None
    video_id = None

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(LeSpider, self).__init__(*args, **kwargs)
        self.url = url
        self.config = ConfigParser.ConfigParser()
        self.config.read("config/config.ini")
        self.uuid = uuid
        self.upload_url = upload_url
        self.callback = callback
        self.check_video_url = check_video_url
        with open("config/database.cnf") as f:
            config = json.load(f)
        db_cls = get_database(config.get("database_type", None))
        self.db = db_cls(**config.get("database", {}))
        self.start_urls.append(url)

    def parse(self, response):
        print response.url

        try:
            self.video_id = self.match_id(response.url)
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.get_video(response)

        '''
        try:
            if self.you_get(response.xpath('//title/text()').extract()[0]):
                return
        except ValueError, e:
            print e
        '''

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': 'mp4/720p/best',
            'outtmpl': 'cache/' + self.uuid + '_%(id)s.%(ext)s',
            # 'ignoreerrors': True,
            'progress_hooks': [self.hooks],
            'max_downloads': 1
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

    def you_get(self, title):
        concatfile = 'cache/' + self.uuid + '.txt'
        if 'news' in self.start_urls[0]:
            command = ['you-get', '--json', self.start_urls[0]]
            print command
            stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            print 'stdout', stdout, 'stderr', stderr

            if stderr:
                dir_path = 'cache/%s/' % self.uuid
                command = ['you-get', '-n', '-o', dir_path, self.start_urls[0]]
                print command
                stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
                print 'stdout', stdout, 'stderr', stderr
                if stderr:
                    return False
                logger.info('[you-get]' + '[uuid]' + self.uuid)

                dirs = os.listdir(dir_path)
                for idx, file in enumerate(sorted(dirs)):
                    new = dir_path + str(idx) + '.mp4'
                    print dir_path + file, new
                    os.rename(dir_path + file, new)
                    open(concatfile, 'a+').write('file ' + string.replace(new, 'cache/', '') + "\n")
            else:
                logger.info('[you-get]' + '[uuid]' + self.uuid)
                video = json.loads(stdout[stdout.find('{'):])
                if 'streams' not in video:
                    return False
                srcs = []
                for key in video['streams'].keys():
                    print key
                    if 'src' in video['streams'][key]:
                        srcs = video['streams'][key]['src']
                        print srcs
                        break

                for idx, src in enumerate(srcs):
                    src_path = 'cache/' + self.uuid + '_' + str(idx) + '.mp4'
                    _, success = service.utils.download_file(src, src_path)
                    if not success:
                        return False
                    open(concatfile, 'a+').write('file ' + string.replace(src_path, 'cache/', '') + "\n")
        else:
            dir_path = 'cache/%s/' % self.uuid
            command = ['you-get', '-n', '-o', dir_path, self.start_urls[0]]
            print command
            stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            print 'stdout', stdout, 'stderr', stderr
            if stderr:
                return False
            logger.info('[you-get]' + '[uuid]' + self.uuid)

            dirs = os.listdir(dir_path)
            for idx, file in enumerate(sorted(dirs)):
                new = dir_path + str(idx) + '.mp4'
                print dir_path + file, new
                os.rename(dir_path + file, new)
                open(concatfile, 'a+').write('file ' + string.replace(new, 'cache/', '') + "\n")
        mp4file = 'cache/' + self.uuid + '.mp4'
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
        raise CloseSpider('you-get success')

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            concatfile = 'cache/' + self.uuid + '.txt'
            open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")
            mp4file = 'cache/' + self.uuid + '.mp4'
            length = service.utils.mergeVideo(mp4file, concatfile)
            print '[merged video duration]', length
            if length == 0:
                raise CloseSpider('merge video failed')
            filesize = os.path.getsize(mp4file)

            endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
            print endpoint, backet, obj
            uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
            print 'uploadResult:', uploadResult
            if not uploadResult:
                raise CloseSpider('upload oss failed')

            logger.warn('[uploadVideo]' + '[uuid]' + self.uuid)
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

    def match_id(self, url):
        letv = r'https?://(?:www\.le\.com/ptv/vplay|sports\.le\.com/(video|news))/(?P<id>\d+)\.html'
        m = re.compile(letv).match(url)
        assert m
        return m.group('id')

    def get_video(self, response):
        # url = "http://player-pc.le.com/mms/out/video/playJson.json?platid=3&splatid=301&tss=no&id=28905707&detect=0&dvtype=1000&accessyx=1&domain=m.le.com&tkey=-1201780529&devid=2554119e19608c72095a3270c29f486d&source=1001&lang=cn&region=cn&isHttps=0&callback=vjs_149394811732481 "
        get_tkey = execjs.compile(open('service/le_new_getMmsKey.js').read())
        tkey = get_tkey.call("mainfunc", int(time.time()))
        video_playjson = {
            "platid": "3",
            "splatid": "301",
            "tss": "ios",
            "id": self.video_id,
            "detect": "0",
            "dvtype": "1000",
            "accessyx": "1",
            "domain": "m.le.com",
            "tkey": tkey,
            "source": "1001",
            "lang": "cn",
            "region": "cn",
            "isHttps": "0",
        }
        # print video_playjson
        video_info_url = "http://player-pc.le.com/mms/out/video/playJson.json?platid=3&splatid=301&tss=no&id=%s&detect=0&dvtype=1000&accessyx=1&domain=m.le.com&tkey=%s&source=1001&lang=cn&region=cn&isHttps=0" % (
        video_playjson["id"], video_playjson["tkey"])
        print video_info_url
        video_info_url_json = requests.get(video_info_url).json()
        domain = video_info_url_json["msgs"]["playurl"]["domain"][0]
        try:
            domain_ = video_info_url_json["msgs"]["playurl"]["dispatch"]["1000"][0]
        except:
            domain_ = video_info_url_json["msgs"]["playurl"]["dispatch"]["350"][0]
        if ('msgs' not in video_info_url_json) or ('playurl' not in video_info_url_json["msgs"]) or (
            'domain' not in video_info_url_json["msgs"]["playurl"]) or (
            'dispatch' not in video_info_url_json["msgs"]["playurl"]):
            return
        title = video_info_url_json["msgs"]["playurl"]["title"]
        if ('msgs' not in video_info_url_json) or ('playurl' not in video_info_url_json["msgs"]) or (
            'title' not in video_info_url_json["msgs"]["playurl"]):
            return
        print title
        pic = video_info_url_json["msgs"]["playurl"]["picAll"]["400*250"]
        if ('msgs' not in video_info_url_json) or ('playurl' not in video_info_url_json["msgs"]) or (
            'picAll' not in video_info_url_json["msgs"]["playurl"]) or (
            '400*250' not in video_info_url_json["msgs"]["playurl"]["picAll"]):
            return
        print pic
        get_video_url_url = domain + domain_ + "&format=1&jsonp=&expect=3&p1=0&p2=04"
        video_url = json.loads(requests.get(get_video_url_url).content[1:-3])["location"]  # 视频url的这个页面的json体中
        print video_url

        video_size = None

        path = 'cache/%s.mp4' % self.uuid
        filesize, success = service.utils.block_download(video_url, path)
        if not success:
            return

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(path, endpoint, backet, obj)
        if not uploadResult:
            return False

        length = service.utils.getVideoLength(path)
        cover = service.utils.get_clip_cover_url(pic, self.uuid)

        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": video_size,
            "cover": cover,
            "title": title
        }
        self.callbacked = service.utils.callback_result(self.callback, data=data)
        logger.info('[finished]' + str(self.callbacked) + '[uuid]' + self.uuid)

        video_data = {
            'title': title,
            'video_id': video_playjson["id"],
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': video_size,
            'video_url': video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('get video success')

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



