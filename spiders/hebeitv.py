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
from bs4 import BeautifulSoup
import requests

reload(sys)
sys.setdefaultencoding('utf-8')

logger = Logger.get_logger('meipai')


class HebeiTVSpider(scrapy.Spider):
    name = "hebeitv"
    allowed_domains = ["hebtv.com"]
    logger = None
    callbacked = False

    def __init__(self, url, uuid, upload_url, callback, check_video_url=None, *args, **kwargs):
        super(HebeiTVSpider, self).__init__(*args, **kwargs)
        self.url = url
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
        print response

        title, pic, video_id, video_url = self.get_video(response)
        print title, pic, video_id, video_url  # 都在这里！

        tmp_path = 'cache/%s.tmp' % self.uuid
        #print tmp_path
        filesize, success = service.utils.download_file(video_url, tmp_path)
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
            'video_url': video_url,
            'easub_uuid': self.uuid
        }
        self.db.save_video(video_data)
        raise CloseSpider('get video success')


    def get_video(self, response):
        info_dict = []
        print 'get video'
        sel = Selector(response).extract()
        soup = BeautifulSoup(sel)
        title = soup.find_all("img",attrs={"height":"300"})[0].attrs['alt']
        pic = soup.find_all("img",attrs={"height":"300"})[0].attrs['src']
        video_info = re.compile('sjvideoURL =".*"').search(sel).group(0)[13:-1].split(";")
        i = 0
        for info in video_info:
            a = '=.*'
            a = re.compile(a).search(info).group(0)
            info_dict.append(a)
            i +=1
            if i > len(video_info):
                break
        id = info_dict[0][1:-4]
        videoType = info_dict[1][1:-4]
        video_id = info_dict[2][1:-4]
        terminalType = info_dict[3][1:]
        #http://mm.hebtv.com:8080/mms4.4.2//videoPlay/CISGetVodPlayURL.jspa?id=279030&videoId=254200&videoType=MOBILE&terminalType=1113161745
        info_url = "http://mm.hebtv.com:8080/mms4.4.2//videoPlay/CISGetVodPlayURL.jspa?id=%s&videoId=%s&videoType=%s&terminalType=%s" % (id,video_id,videoType,terminalType)
        #print info_url
        video_url = str(requests.get(info_url).content)
        b = "http:.*?mp4"
        video_url = re.compile(b).search(video_url).group(0)
        #print video_url
        return title, pic, id, video_url  #这个id才是video_id


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


'''
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
'''