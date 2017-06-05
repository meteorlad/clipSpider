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
import service.mail
import sys
reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('ifeng')


class IfengSpider(scrapy.Spider):
    name = "ifeng"
    allowed_domains = ["ifeng.com"]
    callbacked = False
    video_id = None

    # http://v.ifeng.com/news/society/201611/01a0e556-2a35-4088-a322-6a8a60983fe7.shtml
    # http://dyn.v.ifeng.com/cmpp/video_msg_ipad.js?callback=jQuery1910959290532730704_1478078228964&msg=01a0e556-2a35-4088-a322-6a8a60983fe7&param=playermsg&_=1478078228965
    # http://ips.ifeng.com/video19.ifeng.com/video09/2016/11/01/4333056-102-009-1802.mp4
    # http://news.ifeng.com/a/20160616/49083698_0.shtml
    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(IfengSpider, self).__init__(*args, **kwargs)

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

    def parse(self, response):
        print 'parsePlayurl', self.start_urls[0]
        try:

            vid = response.selector.xpath('//div[@class="vplayMod"]//li/@name').extract() \
                  or response.selector.xpath('//ul[@id="playitem"]/li/@name').extract()
            print 'vid', vid
            if not vid or len(vid) < 1:
                m = re.compile(r"vid\"?\s*:\s*\"(?P<vid>[0-9a-zA-Z-_]{36})\"").search(response.body)
                print 'm', m
                if m:
                    self.video_id = m.group('vid')
                else:
                    self.video_id = self._match_id(self.start_urls[0])
            else:
                print 'get video id from body', vid
                self.video_id = vid[0].strip()
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + self.start_urls[0] + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        info_url = 'http://dyn.v.ifeng.com/cmpp/video_msg_ipad.js?msg=' + self.video_id
        resp = requests.get(info_url)
        content = resp.content
        print content
        video_url = re.search(r"videoplayurl\":\s?\"(?P<video_url>[0-9a-zA-Z-_\.:/]+)\"", content).group('video_url')
        titles = response.selector.xpath('//meta[@name="irTitle"]/@content').extract() \
                 or response.selector.xpath('//title/text()').extract()

        print titles
        title = titles[0].strip()
        print title, video_url
        file_path = 'cache/' + self.uuid + '.mp4'
        filesize, success = service.utils.download_file(video_url, file_path)
        if not success:
            raise CloseSpider('download video failed')

        length = service.utils.getVideoLength(file_path)
        print length
        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        result = service.utils.uploadVideo(file_path, endpoint, backet, obj)
        if not result:
            raise CloseSpider('upload oss failed')

        print 'filesize', filesize

        try:
            img_url = re.search(r"largePoster\":\s?\"(?P<img_url>[0-9a-zA-Z-_\.:/]+)\"", content).group('img_url')
            cover = service.utils.get_clip_cover_url(img_url, self.uuid)
        except:
            cover = ''

        # callback
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
            'video_url': video_url,
            'easub_uuid': self.uuid,
            'cover': cover
        }
        self.db.save_video(video_data)

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

    def _match_id(self, url):
        # http://v.ifeng.com/news/society/201611/01a0e556-2a35-4088-a322-6a8a60983fe7.shtml
        print 'match id', url
        regx = r'https?://.+\.ifeng\.com/[\w/]+/(.+#)?(?P<id>[0-9a-zA-Z-_]{36})'
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
            # subprocess.Popen('rm -rf ' + 'cache/' + self.uuid + '*', shell=True)
            service.utils.remove_all_files('cache/', self.uuid)
