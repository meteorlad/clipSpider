# -*- coding: utf-8 -*-
import scrapy
import ConfigParser
import time
import re
import requests
import json
from service.logger import Logger
from scrapy.exceptions import CloseSpider
from service.database import get_database
import service.utils
import subprocess
import hashlib
import BeautifulSoup
import youtube_dl
import string
import os
import service.mail
import sys
import execjs

reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('bilibili')


class BilibiliSpider(scrapy.Spider):
    name = "bilibili"
    allowed_domains = ["bilibili.com"]
    callbacked = False
    video_id = None

    # http://www.bilibili.com/m/html5?aid=6032244&page=1
    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(BilibiliSpider, self).__init__(*args, **kwargs)

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
        #浏览器复制bilibili的cookie
        self.header = {'Cookie':'pgv_pvi=1211209728; UM_distinctid=15b05e151043a-02ba96c782db3a-6a11157a-100200-15b05e1510774; fts=1490452304; sid=56ptt8q6; rpdid=owkliqwxxldoploikssww; finger=7360d3c2; buvid3=927004CE-F581-4EC0-91C4-5832AC9038D025906infoc; pgv_si=s5848608768; purl_token=bilibili_1496493155'}

    def parse(self, response):
        print 'parsePlayurl', response.url
        try:
            title = response.selector.xpath('//h1/@title').extract()[0].strip()
            self.video_id, page ,is_url = self._match_id(self.start_urls[0])
            if is_url == 1:
                url = self.video_id
                print url, title
                self.get_bangumi_video(url, title)
                return
            else:
                url = None
        except:
            raise CloseSpider('link not supported')

        print title, self.video_id, page, is_url, url
        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.get_video(self.video_id, page, title)

        if self.callbacked:
            return

        if self.you_get():
            return

        m = re.search(r'sid%2F(?P<source_id>\w+)', response.body)
        if m:
            self.video_id = m.group('source_id')
            self.start_urls[0] = "http://v.youku.com/v_show/id_%s.html" % self.video_id
            print 'youku url', self.start_urls[0]
            if self.check_db():
                return

        ydl_opts = {
            'writeinfojson': True,
            'skip_download': False,
            'format': '1/22/best',
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
                "cover": '',
                "title": result[1]
            }
            self.callbacked = service.utils.callback_result(self.callback, data=data)
            return True

    def you_get(self, format_type='mp4'):
        command = ['you-get', '-F', format_type, '--json', self.start_urls[0]]
        print command
        stdout, stderr = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
        print 'stdout', stdout, 'stderr', stderr

        if stderr and format_type == 'mp4':
            return self.you_get(format_type='flvhd')
        elif stderr or len(stdout) < 2:
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

    def get_bangumi_video(self, url, title):
        print 'get bangumi video'
        s = re.search(r'play#(?P<id>\d+)', url)
        id = s.group('id')
        #https://bangumi.bilibili.com/web_api/episode/105215.json
        info_url = 'https://bangumi.bilibili.com/web_api/episode/' + id + '.json'
        cid = requests.get(info_url).json()['result']['currentEpisode']['danmaku']
        sign_url = self.getsign_url(cid)
        try:
            info = requests.get(sign_url).json()
            video_url = info['durl'][0]['url']
            file_size = info['durl'][0]['size']
            print video_url
        except:
            print 'url no exist'
            return

        file_path = 'cache/' + str(self.uuid) + '.tmp'
        concatfile = 'cache/' + str(self.uuid) + '.txt'
        mp4file = 'cache/' + str(self.uuid) + '.mp4'
        filesize, success = service.utils.block_download(video_url, file_path)

        if not success:
            raise CloseSpider('download video failed')
        open(concatfile, 'a+').write('file ' + string.replace(file_path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(mp4file, concatfile)

        print length
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

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
            'video_id': id,
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)

    def get_video(self, aid, page, title):
        print 'get video'
        cidurl = 'https://www.bilibili.com/widget/getPageList?aid=' + aid
        cidinfos = requests.get(cidurl).json()
        if page == 0 or page == 1:
            cidinfo =  cidinfos[0]
        else:
            cidinfo = cidinfos[int(page)-1]
        cid = cidinfo['cid']
        sign_url = self.getsign_url(cid)
        print sign_url
        try:
            info = requests.get(sign_url).json()
            print info
            video_url = info['durl'][0]['url']
            file_size = info['durl'][0]['size']
            print video_url
        except:
            print 'url no exist'
            return
        file_path = 'cache/' + str(self.uuid) + '.tmp'
        concatfile = 'cache/' + str(self.uuid) + '.txt'
        mp4file = 'cache/' + str(self.uuid) + '.mp4'
        filesize, success = service.utils.block_download(video_url, file_path, headers={
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, sdch, br",
            "Accept-Language": "zh-CN,zh;q=0.8,en;q=0.6,zh-TW;q=0.4",
            "Connection": "keep-alive",
            "DNT": 1,
            "Origin": "null",
            "Referer": self.start_urls[0],
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36Accept:*/*"
        }, file_size=file_size)

        if not success:
            raise CloseSpider('download video failed')
        open(concatfile, 'a+').write('file ' + string.replace(file_path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(mp4file, concatfile)
        print length
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

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
            'video_id': aid,
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': filesize,
            'video_url': video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)

    def getsign_url(self, cid, appkey='84956560bc028eb7', bilibili_key='94aba54af9065f71de72f5508f1cd42e'):
        payload = 'appkey=%s&cid=%s&otype=json&quality=2' % (appkey, cid)
        sign = hashlib.md5((payload + bilibili_key).encode('utf-8')).hexdigest()
        return 'https://interface.bilibili.com/playurl?' + payload + '&sign=' + sign

    def hooks(self, d):

        if d['status'] == 'finished':
            filename = d['filename']
            l = filename.split('.')
            ext = l[len(l) - 1]
            print ext
            jsonfile = string.replace(filename, ext, 'info.json')
            info = json.loads(open(jsonfile).read())
            if 'n_entries' not in info:
                self.save(info, filename)
            else:
                concatfile = 'cache/' + self.uuid + '.txt'
                open(concatfile, 'a+').write('file ' + string.replace(filename, 'cache/', '') + "\n")
                if info['playlist_index'] == info['n_entries']:
                    mp4file = 'cache/' + self.uuid + '.mp4'
                    length = service.utils.mergeVideo(mp4file, concatfile)
                    print '[merged video duration]', length
                    if length == 0:
                        raise CloseSpider('merge video failed')
                    self.save(info, mp4file)

        if d['status'] == 'error':
            raise CloseSpider('download failed')

    def save(self, info, mp4file):
        filesize = os.path.getsize(mp4file)
        length = service.utils.getVideoLength(mp4file)
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(mp4file, endpoint, backet, obj)
        print 'uploadResult:', uploadResult
        if not uploadResult:
            raise CloseSpider('upload oss failed')

        logger.warn('[uploadVideo]' + '[uuid]' + self.uuid)

        data = {
            "video_id": self.uuid,
            "state": 1,
            "message": u'成功',
            "length": length,
            "play_id": self.uuid,
            "size": filesize,
            "cover": '',
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
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('finished')

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

    def _match_id(self, url):
        print 'match id'
        #regx = r'https?://(?:www\.|bangumi\.|)bilibili\.(?:tv|com)/(?:video/av|anime/v/)(?P<id>\d+)'
        regx = r'https?://(?:www\.|bangumi\.|)bilibili\.(?:tv|com)/(?:video/av|anime/v/|anime/)(?P<id>\d+).*'
        m = re.compile(regx).match(url)
        if 'play#' in m.group(0):
            is_url = 1
            page = 0
            return m.group(0), page ,is_url
        else:
            is_url = 0
            pass
        assert m
        s = re.search(r'index_(?P<page>\d+)\.html', url)
        if s:
            page = s.group('page')
        else:
            page = 0
        return m.group('id'), page , is_url



