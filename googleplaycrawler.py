from __future__ import print_function

import os
import sys
import time
import argparse
from datetime import datetime
import requests
import csv
import logging
import apkfetch_pb2
from util import encrypt
from lxml import html

### tweak these values according to your needs ###
DOWNLOAD_APPS = True  # should the crawler download the apk files?
STORE_INFO = True  # should the crawler store the information in the .csv files?
REVIEWS = 50  # amount of reviews to get per app


DOWNLOAD_FOLDER_PATH = 'apps/'

GOOGLE_LOGIN_URL = 'https://android.clients.google.com/auth'
GOOGLE_CHECKIN_URL = 'https://android.clients.google.com/checkin'
GOOGLE_DETAILS_URL = 'https://android.clients.google.com/fdfe/details'
GOOGLE_BULKDETAILS_URL = 'https://android.clients.google.com/fdfe/bulkDetails'
GOOGLE_DELIVERY_URL = 'https://android.clients.google.com/fdfe/delivery'
GOOGLE_PURCHASE_URL = 'https://android.clients.google.com/fdfe/purchase'
GOOGLE_BROWSE_URL = 'https://android.clients.google.com/fdfe/browse'
GOOGLE_LIST_URL = 'https://android.clients.google.com/fdfe/list'
GOOGLE_REVIEWS_URL = "https://android.clients.google.com/fdfe/rev"
GOOGLE_FDFE_URL = "https://android.clients.google.com/fdfe"

LOGIN_USER_AGENT = 'GoogleLoginService/1.3 (gts3llte)'
MARKET_USER_AGENT = 'Android-Finsky/5.7.10 (api=3,versionCode=80371000,sdk=24,device=falcon_umts,hardware=qcom,product=falcon_reteu,platformVersionRelease=4.4.4,model=XT1032,buildId=KXB21.14-L1.40,isWideScreen=0)'
CHECKIN_USER_AGENT = 'Android-Checkin/2.0 (gts3llte)'
DOWNLOAD_USER_AGENT = 'AndroidDownloadManager/9 (Linux; U; Android 9; XT1032 Build/KXB21.14-L1.40)'


def num_to_hex(num):
    hex_str = format(num, 'x')
    length = len(hex_str)
    return hex_str.zfill(length + length % 2)


class GooglePlayCrawler(object):

    def __init__(self):
        self.session = requests.Session()
        self.user = self.password = self.android_id = self.token = self.auth = None
        self.iter = 1

    def request_service(self, service, app, user_agent=LOGIN_USER_AGENT):
        """
        requesting a login service from google
        @service: the service to request, like ac2dm
        @app: the app to request to
        @user_agent: the user agent
        """

        self.session.headers.update({'User-Agent': user_agent,
                                     'Content-Type': 'application/x-www-form-urlencoded'})

        if self.android_id:
            self.session.headers.update({'device': self.android_id})

        data = {'accountType': 'HOSTED_OR_GOOGLE',
                'has_permission': '1',
                'add_account': '1',
                'get_accountid': '1',
                'service': service,
                'app': app,
                'source': 'android',
                'Email': self.user}

        if self.android_id:
            data['androidId'] = self.android_id

        data['EncryptedPasswd'] = self.token or encrypt(self.user, self.password)

        response = self.session.post(GOOGLE_LOGIN_URL, data=data, allow_redirects=True)
        response_values = dict([line.split('=', 1) for line in response.text.splitlines()])

        if 'Error' in response_values:
            error_msg = response_values.get('ErrorDetail', None) or response_values.get('Error')
            if 'Url' in response_values:
                error_msg += '\n\nTo resolve the issue, visit: ' + response_values['Url']
                error_msg += '\n\nOr try: https://accounts.google.com/b/0/DisplayUnlockCaptcha'
            raise RuntimeError(error_msg)
        elif 'Auth' not in response_values:
            raise RuntimeError('Could not login')

        return response_values.get('Token', None), response_values.get('Auth')

    def login(self, user, password, android_id=None):
        """
        login using googles as2dm authentication system
        @user: email
        @passwd: password
        @androidid: android id
        """

        self.user = user
        self.password = password
        self.android_id = android_id

        self.token, self.auth = self.request_service('ac2dm', 'com.google.android.gsf')

        logging.info('token: ' + self.token)

        _, self.auth = self.request_service('androidmarket', 'com.android.vending', MARKET_USER_AGENT)
        logging.info('auth: ' + self.auth)

        return self.auth is not None

    def details(self, package_name):
        """
        performs a GET request to get the details of a specific app
        @package_name: the app to get details from
        """

        headers = {'X-DFE-Device-Id': self.android_id,
                   'X-DFE-Client-Id': 'am-android-google',
                   'Accept-Encoding': '',
                   'Host': 'android.clients.google.com',
                   'Authorization': 'GoogleLogin Auth=' + self.auth,
                   'User-Agent': MARKET_USER_AGENT}

        params = {'doc': package_name}
        response = self.session.get(GOOGLE_DETAILS_URL, params=params, headers=headers, allow_redirects=True)

        details_response = apkfetch_pb2.ResponseWrapper()
        details_response.ParseFromString(response.content)
        # print(details_response.payload.detailsResponse.docV2)
        details = details_response.payload.detailsResponse.docV2
        if not details:
            RuntimeError('Could not get details for: ' + package_name)
        if details_response.commands.displayErrorMessage != "":
            RuntimeError(
                'error getting details: ' + details_response.commands.displayErrorMessage + " for: " + package_name)
        return details

    def reviews(self, package_name, amount=50):
        """
        performs a GET request to get the reviews of a specific app
        @package_name: the app to get reviews from
        @amount: amount of reviews to get
        """

        headers = {'X-DFE-Device-Id': self.android_id,
                   'X-DFE-Client-Id': 'am-android-google',
                   'Accept-Encoding': '',
                   'Host': 'android.clients.google.com',
                   'Authorization': 'GoogleLogin Auth=' + self.auth,
                   'User-Agent': MARKET_USER_AGENT}

        params = {'doc': package_name,
                  'n': amount}
        response = self.session.get(GOOGLE_REVIEWS_URL, params=params, headers=headers, allow_redirects=True)

        review_response = apkfetch_pb2.ResponseWrapper()
        review_response.ParseFromString(response.content)

        if not review_response:
            RuntimeError('Could not get reviews for: ' + package_name)
        if review_response.commands.displayErrorMessage != "":
            RuntimeError(
                'error getting reviews: ' + review_response.commands.displayErrorMessage + " for: " + package_name)
        return review_response.payload.reviewResponse.getResponse

    def get_download_url(self, package_name, version_code):
        """
        performs a GET request to get the download url of a specific app
        @package_name: the app to get the download url from
        @version_code: the version of the app to download
        """

        headers = {'X-DFE-Device-Id': self.android_id,
                   'X-DFE-Client-Id': 'am-android-google',
                   'Accept-Encoding': '',
                   'Host': 'android.clients.google.com',
                   'Authorization': 'GoogleLogin Auth=' + self.auth,
                   'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}

        data = {'doc': package_name,
                'ot': '1',
                'vc': version_code}

        response = self.session.get(GOOGLE_DELIVERY_URL, params=data, verify=True, headers=headers,
                                    allow_redirects=True)

        delivery_response = apkfetch_pb2.ResponseWrapper()
        delivery_response.ParseFromString(response.content)

        if not delivery_response:
            logging.error('Could not get download url for: ' + package_name)
        if delivery_response.commands.displayErrorMessage != "":
            logging.error(
                'error getting download url: ' + delivery_response.commands.displayErrorMessage + " for: " + package_name)
        return delivery_response.payload.deliveryResponse.appDeliveryData.downloadUrl

    def purchase(self, package_name, version_code):
        """
        performs a GET request to get the download token of a specific app and complete the purchase
        @package_name: the app to get the download token from
        @version_code: the version of the app to get the download token from
        """

        if version_code is None:
            raise RuntimeError('no version code for purchase')

        headers = {
            "X-DFE-Encoded-Targets": "CAEScFfqlIEG6gUYogFWrAISK1WDAg+hAZoCDgIU1gYEOIACFkLMAeQBnASLATlASUuyAyqCAjY5igOMBQzfA/IClwFbApUC4ANbtgKVAS7OAX8YswHFBhgDwAOPAmGEBt4OfKkB5weSB5AFASkiN68akgMaxAMSAQEBA9kBO7UBFE1KVwIDBGs3go6BBgEBAgMECQgJAQIEAQMEAQMBBQEBBAUEFQYCBgUEAwMBDwIBAgOrARwBEwMEAg0mrwESfTEcAQEKG4EBMxghChMBDwYGASI3hAEODEwXCVh/EREZA4sBYwEdFAgIIwkQcGQRDzQ2fTC2AjfVAQIBAYoBGRg2FhYFBwEqNzACJShzFFblAo0CFxpFNBzaAd0DHjIRI4sBJZcBPdwBCQGhAUd2A7kBLBVPngEECHl0UEUMtQETigHMAgUFCc0BBUUlTywdHDgBiAJ+vgKhAU0uAcYCAWQ/5ALUAw1UwQHUBpIBCdQDhgL4AY4CBQICjARbGFBGWzA1CAEMOQH+BRAOCAZywAIDyQZ2MgM3BxsoAgUEBwcHFia3AgcGTBwHBYwBAlcBggFxSGgIrAEEBw4QEqUCASsWadsHCgUCBQMD7QICA3tXCUw7ugJZAwGyAUwpIwM5AwkDBQMJA5sBCw8BNxBVVBwVKhebARkBAwsQEAgEAhESAgQJEBCZATMdzgEBBwG8AQQYKSMUkAEDAwY/CTs4/wEaAUt1AwEDAQUBAgIEAwYEDx1dB2wGeBFgTQ",
            "User-Agent": MARKET_USER_AGENT,
            'X-DFE-Device-Id': self.android_id,
            "X-DFE-Client-Id": "am-android-google",
            'Host': 'android.clients.google.com',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            "X-DFE-MCCMNC": "310260",
            "X-DFE-Network-Type": "4",
            "X-DFE-Content-Filters": "",
            "X-DFE-Request-Params": "timeoutMs=4000",
            'Authorization': 'GoogleLogin Auth=' + self.auth,
            'Accept-Encoding': '',
        }

        params = {'ot': 1,
                  'doc': package_name,
                  'vc': version_code}

        response = requests.post(GOOGLE_PURCHASE_URL, headers=headers,
                                 params=params, verify=True,
                                 timeout=60)

        response = apkfetch_pb2.ResponseWrapper.FromString(response.content)
        if response.commands.displayErrorMessage != "":
            RuntimeError(
                'error performing purchase: ' + response.commands.displayErrorMessage + " for: " + package_name)
        else:
            download_token = response.payload.buyResponse.downloadToken
            return download_token

    def fetch(self, package_name, version_code, apk_fn=None):
        """
        download the app, by getting a download url.
        @package_name: the app to download
        @version_code: the version of the app to download
        @apk_fn: predefined name, package_name by default
        """

        url = self.get_download_url(package_name, version_code)
        if not url:
            return 0

        response = self.session.get(url, headers={'User-Agent': DOWNLOAD_USER_AGENT},
                                    stream=True, allow_redirects=True)

        logging.info("downloading...")
        apk_fn = apk_fn or (DOWNLOAD_FOLDER_PATH + package_name + '.apk')
        if os.path.exists(apk_fn):
            os.remove(apk_fn)

        with open(apk_fn, 'wb') as fp:
            for chunk in response.iter_content(chunk_size=5 * 1024):
                if chunk:
                    fp.write(chunk)
                    fp.flush()
            fp.close()

        return os.path.exists(apk_fn)

    def getrelated(self, browsestream):
        """
        get the list of apps under the "more you might like" section under app details
        @browsestream: the link from the app details to request the list of related apps
        """

        headers = {'X-DFE-Device-Id': self.android_id,
                   'X-DFE-Client-Id': 'am-android-google',
                   'Accept-Encoding': '',
                   'Host': 'android.clients.google.com',
                   'Authorization': 'GoogleLogin Auth=' + self.auth,
                   'User-Agent': MARKET_USER_AGENT}

        response = self.session.get(GOOGLE_FDFE_URL + "/" + browsestream, params=None, headers=headers,
                                    allow_redirects=True)

        related_response = apkfetch_pb2.ResponseWrapper()
        related_response.ParseFromString(response.content)
        # print(related_response.preFetch[0].response.payload.listResponse.doc)

        if not related_response:
            RuntimeError('Could not get related apps for')
        if related_response.commands.displayErrorMessage != "":
            RuntimeError('error getting related apps: ' + related_response.commands.displayErrorMessage)
        return related_response.preFetch[0].response.payload.listResponse.doc[0]

    def get_category(self, url):
        page = requests.get(url)
        tree = html.fromstring(page.content)
        category = tree.xpath('//a[@itemprop="genre"]/text()')
        return category

    def get_android_version(self, url):
        page = requests.get(url)
        tree = html.fromstring(page.content)
        version = tree.xpath('//span[@class="htlgb"]/text()')
        return version[4]

    def load_visited_apps(self):
        """
        load all apps previously visited from the appinfo.csv file
        """

        with open("apps/data/appinfo.csv", "r") as csvfile:
            file = csv.reader(csvfile, delimiter=',', quotechar='"')
            visited_apps = []

            for row in file:
                visited_apps += [row[0]]

            csvfile.close()

        # pop the column names
        visited_apps.pop(0)

        logging.info(
            str(len(visited_apps)) + " previously crawled apps loaded. This crawler won't crawl through these apps.")
        return visited_apps

    def store(self, details, reviews, related_apps):
        """
        store the details and reviews of an app into a .csv file
        @details: the list of details of a specific app
        @reviews: the list of reviews from a specific app
        @related_apps: a list of related apps
        """

        with open("apps/data/appinfo.csv", "a") as csv_file:

            related_apps_string = ""
            for app in related_apps:
                related_apps_string += app.docid + ","
            related_apps_string = related_apps_string[:-1]

            url = "https://play.google.com/store/apps/details?id=" + details.docid + "&hl=en"

            category_string = ""
            for category in self.get_category(url):
                category_string += category + ","
            category_string = category_string[:-1]

            android_version = self.get_android_version(url)

            file = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            file.writerow([details.docid, details.backendDocid, details.title, details.descriptionHtml,
                           details.descriptionShort,
                           url, "https://android.clients.google.com/fdfe/" + details.relatedLinks.youMightAlsoLike.url2,
                           related_apps_string, category_string, details.details.appDetails.appType,
                           details.offer[0].micros, details.offer[0].currencyCode,
                           details.details.appDetails.numDownloads, details.relatedLinks.rated.label,
                           details.aggregateRating.starRating, details.aggregateRating.ratingsCount,
                           details.aggregateRating.fiveStarRatings,
                           details.aggregateRating.fourStarRatings, details.aggregateRating.threeStarRatings,
                           details.aggregateRating.twoStarRatings, details.aggregateRating.oneStarRatings,
                           details.details.appDetails.developerAddress,
                           details.details.appDetails.developerEmail, details.details.appDetails.developerWebsite,
                           details.details.appDetails.developerName, details.creator,
                           details.relatedLinks.privacyPolicyUrl,
                           details.details.appDetails.versionCode, details.details.appDetails.versionString,
                           details.details.appDetails.uploadDate,
                           details.details.appDetails.recentChangesHtml, android_version,
                           details.details.appDetails.installationSize, details.details.appDetails.unstable,
                           details.details.appDetails.hasInstantLink, details.details.appDetails.containsAds])
            csv_file.close()

        with open("apps/data/permissions.csv", "a") as csv_file:
            with open("templatePermissions.csv", "r") as permissionsFile:
                permissions = csv.reader(permissionsFile, delimiter=',', quotechar='"')
                file = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
                haspermission = [details.docid]
                for row in permissions:
                    if row[0] in details.details.appDetails.permission:
                        haspermission += [1]
                    else:
                        haspermission += [0]

                file.writerow(haspermission)
                permissionsFile.close()
            csv_file.close()

        with open("apps/data/externalpermissions.csv", "a") as csv_file:
            file = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            external_permissions = [details.docid]
            for row in details.details.appDetails.permission:
                if not row.startswith("android.permission."):
                    external_permissions += [row]

            file.writerow(external_permissions)
            csv_file.close()

        with open("apps/data/images.csv", "a") as csv_file:
            file = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
            image_urls = [details.docid]
            for image in details.image:
                image_urls += [image.imageUrl]

            file.writerow(image_urls)
            csv_file.close()

        with open("apps/data/reviews.csv", "a") as csv_file:
            file = csv.writer(csv_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

            for data in reviews.review:
                file.writerow([details.docid, data.documentVersion, data.timestampMsec, data.starRating, data.comment,
                               data.userProfile.personId, data.userProfile.name, data.userProfile.image[0].imageUrl])

            csv_file.close()

    def visitapp(self, package_name):
        """
        gets and stores the information and reviews of a specific package and downloads the apkfile
        @package_name: the package to start from
        """

        logging.info("started crawling through " + package_name + " on iteration: {}".format(self.iter))
        print("started crawling through " + package_name + " on iteration: {}".format(self.iter))
        details = self.details(package_name)
        version = details.details.appDetails.versionCode
        reviews = self.reviews(package_name, REVIEWS)

        if not DOWNLOAD_APPS:
            logging.info("downloading is turned off")
            time.sleep(5)
        elif details.offer[0].micros > 0:
            logging.warning("This app needs to be paid for in order to download")
        else:
            if self.purchase(package_name, version):
                logging.info("successful purchase")
            if self.fetch(package_name, version):
                logging.info('Downloaded version {}'.format(version))

        related_apps = self.getrelated(details.relatedLinks.youMightAlsoLike.url2)

        if STORE_INFO:
            self.store(details, reviews, related_apps.child)

        return related_apps.child

    def crawl(self, package_name, visited_packages, max_iterations=1):
        """
        crawls through the google play store, provided with a starting package
        @package_name: the package to start from
        @visitedpackages: a list of packages already visited
        """

        time.sleep(1)

        try:
            related_apps = self.visitapp(package_name)
        except Exception as e:
            print('Error:', str(e))
            logging.error('error: ' + str(e) + ".\n Probably a server timeout. Waiting and trying again.")
            time.sleep(10)

            try:
                related_apps = self.visitapp(package_name)
            except Exception as e:
                print('Error:', str(e))
                logging.critical('critical error: ' + str(e) + ".\n Second try failed. Skipping this app and moving "
                                                               "on to the next")
                return

        for app in related_apps:
            if app.docid not in visited_packages and self.iter < max_iterations:
                self.iter += 1
                visited_packages += [app.docid]
                self.crawl(app.docid, visited_packages)


def main(argv):
    # parse arguments
    parser = argparse.ArgumentParser(add_help=False, description=(
        'Download APK files from the google play store and retrieve their information'))
    parser.add_argument('--help', '-h', action='help', default=argparse.SUPPRESS,
                        help='Show this help message and exit')
    parser.add_argument('--user', '-u', help='Google username')
    parser.add_argument('--passwd', '-p', help='Google password')
    parser.add_argument('--androidid', '-a', help='AndroidID')
    parser.add_argument('--package', '-k', help='Package name of the app')
    parser.add_argument('--iterations', '-i', help='Amount of apps you want to crawl through')

    # prepare logging file
    logging.basicConfig(filename=datetime.now().strftime("logs/%Y-%m-%d_%H:%M:%S.log"), level=logging.INFO,
                        format="%(asctime)s - %(levelname)s: %(message)s")

    # start timing the program
    start_time = time.time()

    try:
        # assign parsed values
        args = parser.parse_args(sys.argv[1:])

        user = args.user
        password = args.passwd
        android_id = args.androidid
        package = args.package
        max_iterations = args.iterations

        if not user or not password or not package or not android_id:
            parser.print_usage()
            raise ValueError('user, passwd, androidid and package are required options. android ID can be found using '
                             'Device id on your android device using an app from the playstore')

        # create class
        apk = GooglePlayCrawler()
        print("crawling through the playstore")

        # login
        apk.login(user, password, android_id)

        if not android_id and apk.android_id:
            print('AndroidID', apk.android_id)

        time.sleep(1)

    except Exception as e:
        print('authentication error:', str(e))
        logging.critical('authentication error:' + str(e) + ". terminating program")
        sys.exit(1)

    visited_apps = apk.load_visited_apps()
    if package not in visited_apps:
        apk.crawl(package, visited_apps, max_iterations)
    else:
        print("package has been visited before. Pick a new package to start from or run resetcsvfiles.py to start over")
        logging.info(
            "package has been visited before. Pick a new package to start from or run resetcsvfiles.py to start over")

    print("finished crawling")
    print("crawled through {} apps in {:.1f} seconds".format(apk.iter, time.time() - start_time))
    logging.info("crawled through {} apps in {:.1f} seconds".format(apk.iter, time.time() - start_time))


if __name__ == "__main__":
    main(sys.argv[1:])