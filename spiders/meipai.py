# -*- coding: utf-8 -*-
import scrapy
from scrapy.selector import Selector
import ConfigParser
import string
import time
import json
import re
from service.database import get_database
import service.utils
from service.logger import Logger
from scrapy.exceptions import CloseSpider
import service.mail
import sys
import execjs
from bs4 import BeautifulSoup
import base64

reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('meipai')


class TudouSpider(scrapy.Spider):
    name = "meipai"
    allowed_domains = ["meipai.com"]
    logger = None
    callbacked = False

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(TudouSpider, self).__init__(*args, **kwargs)
        self.start_urls.append(url)
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

    def parse(self, response):
        print response.url

        try:
            self.video_id = self._match_id(self.start_urls[0])
        except AssertionError, e:
            raise CloseSpider('link not supported')

        logger.warn('[parse]' + response.url + ' [uuid]' + self.uuid + ' [video_id]' + self.video_id)
        if self.check_db():
            return

        self.get_video(response)

        if self.callbacked:
            return

    def get_video(self, response):
        sel = Selector(response).extract()
        soup = BeautifulSoup(sel)
        data1 = soup.find(id='detailVideo', attrs={'data-video': True})
        data2 = soup.find(itemprop="image", attrs={'alt': True, 'src': True})
        data_video = data1['data-video']  # 获取未解码的视频地址
        pic = data2['src']
        title = data2['alt']
        print self.video_id, data_video, title, pic
        meipaiMP4decode = execjs.compile(open('service/meipai_dataVideoDecode.js').read())  # 对视频地址进行解码
        meipaiMP4data_video = meipaiMP4decode.call("decode", data_video)
        meipaiMP4data_video_url = base64.b64decode(meipaiMP4data_video)
        print meipaiMP4data_video_url

        if not meipaiMP4data_video_url:
            return False

        tmp_path = 'cache/%s.tmp' % self.uuid
        print tmp_path, meipaiMP4data_video_url
        filesize, success = service.utils.download_file(meipaiMP4data_video_url, tmp_path)
        if not success:
            return False

        video_size = None

        path = 'cache/%s.mp4' % self.uuid
        concatfile = 'cache/' + self.uuid + '.txt'
        open(concatfile, 'a+').write('file ' + string.replace(tmp_path, 'cache/', '') + "\n")
        length = service.utils.mergeVideo(path, concatfile)

        endpoint, backet, obj = service.utils.paseUploadUrl(self.upload_url)
        print endpoint, backet, obj
        uploadResult = service.utils.uploadVideo(path, endpoint, backet, obj)
        if not uploadResult:
            return False

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
            'video_id': self.video_id,
            'author': self.name,
            'publish': time.strftime('%Y-%m-%d %H:%M:%S'),
            'page_url': self.start_urls[0],
            'video_length': length,
            'video_size': video_size,
            'video_url': meipaiMP4data_video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('get video success')

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
        meipai = 'http://www.meipai.com/media/(?P<id>\d*)'
        m = re.compile(meipai).match(url)
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
