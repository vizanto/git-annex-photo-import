#!/usr/local/bin/python3

import argparse
from collections import defaultdict
import json
import logging
import os
import pprint
import shutil
import subprocess
import sys
import tempfile
import time

import exifread
USE_EXIFREAD = True

from urllib.request import urlopen
# from urllib import urlopen

#TODO py3.3 uses shlex.quote
from pipes import quote

USE_STAGING = False # True # True means copy then import the copy, avoids deleting the original

WANTED_KEYS_EXIFTOOL = ['CreateDate', 'GPSLongitude', 'GPSLongitudeRef', 'GPSLatitude', 'GPSLatitudeRef', 'ImageDescription', 'Model', 'Year', 'Month', 'Day', 'SourceFile', 'GPSImgDirection', 'GPSImgDirectionRef', 'GPSAltitude', 'GPSAltitudeRef']
# for reference while hacking:
ALL_KEYS_EXIFTOOL = ['YResolution', 'GPSImgDirectionRef', 'ResolutionUnit', 'FilePermissions', 'GPSLongitude', 'Make', 'SourceFile', 'FlashpixVersion', 'SceneCaptureType', 'ThumbnailImage', 'SubjectArea', 'Directory', 'YCbCrPositioning', 'XResolution', 'GPSPosition', 'Aperture', 'Compression', 'GPSAltitudeRef', 'GPSTimeStamp', 'BitsPerSample', 'GPSImgDirection', 'ModifyDate', 'LightValue', 'ExposureProgram', 'ShutterSpeed', 'ShutterSpeedValue', 'ColorSpace', 'FocalLength35efl', 'ExifImageWidth', 'ThumbnailOffset', 'DateTimeOriginal', 'ImageWidth', 'ThumbnailLength', 'CreateDate', 'MIMEType', 'SensingMethod', 'FNumber', 'Flash', 'ApertureValue', 'FocalLength', 'FileType', 'ImageDescription', 'ComponentsConfiguration', 'ExifByteOrder', 'FileAccessDate', 'ExifImageHeight', 'ImageHeight', 'EncodingProcess', 'FileInodeChangeDate', 'Model', 'ExifToolVersion', 'GPSLongitudeRef', 'YCbCrSubSampling', 'Software', 'ExposureTime', 'Orientation', 'MeteringMode', 'GPSLatitude', 'Sharpness', 'GPSLatitudeRef', 'ColorComponents', 'FileName', 'WhiteBalance', 'GPSAltitude', 'FileSize', 'FileModifyDate', 'ExposureMode', 'ImageSize', 'ISO', 'DigitalZoomRatio', 'ExifVersion']


WANTED_KEYS_EXIFREAD = ['EXIF DateTimeOriginal', 'GPS GPSLongitude', 'GPS GPSLongitudeRef', 'GPS GPSLatitude', 'GPS GPSLatitudeRef', 'Image ImageDescription', 'Image Model', 'GPS GPSImgDirection', 'GPS GPSImgDirectionRef', 'GPS GPSAltitude', 'GPS GPSAltitudeRef']

GEOCODE_KEYS = ['County', 'Formatted Address', 'State', 'Country', 'Locality', 'Neighborhood', 'Postal Code', 'Route']
WANTED_KEYS = ['Year', 'Month', 'Day', 'SourceFile'] + WANTED_KEYS_EXIFREAD + GEOCODE_KEYS

CREATION_DATE_KEY = 'EXIF DateTimeOriginal'

ROWS, COLS = os.popen('stty size', 'r').read().split()

def show_status(at, total, extra=""):

    pct = float(at) / total
    numbers = "{} of {}: ".format(at, total)
    remaining = int(COLS) - len(numbers) - len(extra) - 2
    prog = "=" * (int(pct * remaining) )
    space = " " * int((1.0 - pct) * remaining)
    bar = "[{}{}]".format(prog, space)
    sys.stderr.write("\r{}{}{}".format(numbers, extra, bar))
    sys.stderr.flush()


def setup_logging(echo_to_stderr=False):
    logging.basicConfig(filename='git-annex-photo-import.log', level=logging.DEBUG)
    if echo_to_stderr:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        logging.getLogger('').addHandler(console)


def timestruct_from_metadata(m, sourcefilename):
    if CREATION_DATE_KEY not in m:
        logging.debug("no EXIF creation date for {}, using mtime.".format(sourcefilename))
        st = os.stat(sourcefilename)
        timestruct = time.localtime(st.st_mtime)
        # NOTE: os x ctime seems odd so I go with mtime, which is what the Finder reports as "created" anyway
        return timestruct
    datetimestr = m[CREATION_DATE_KEY]
    if USE_EXIFREAD:
        datetimestr = datetimestr.values

    expected_time_format = "%Y:%m:%d %H:%M:%S"
    try:
        timestruct = time.strptime(datetimestr, expected_time_format)
    except ValueError:
        # let's hope the seconds is just weird:
        length_without_seconds = len("2007:05:05 11:39")
        newdts = datetimestr[:length_without_seconds]
        logging.warning("NOTE: datetime in wrong format ({}),"
                        " trying again with".format(datetimestr, newdts))
        timestruct = time.strptime(newdts, "%Y:%m:%d %H:%M")
    return timestruct


def filename_from_metadata(m):
    sourcefilename = m["SourceFile"]
    basename = os.path.basename(sourcefilename)
    base, extension = os.path.splitext(basename)
    extension = extension.lower()
    timestruct = timestruct_from_metadata(m, sourcefilename)
    filename = time.strftime("%Y-%B-%d_%H_%M_%S", timestruct)
    if basename.startswith(filename):
        return basename
    else:
        return "{}-{}{}".format(filename, base, extension)


def import_files(filenames):
    if len(filenames) == 0:
        return False
    # TODO: calling import per file is less efficient but helps with
    # errors. is there a better way?

    import_count = 0
    skip_count = 0
    for filename in filenames:
        try:
            logging.debug("importing {}".format(filename))

            if os.path.islink(filename): # Already imported: symlink to annex object
                logging.debug("- skipping (symlink) file.")
                skip_count += 1
            else:
                cmd = "git-annex import '{}'".format(filename) if USE_STAGING else "git-annex add '{}'".format(filename)
                out = subprocess.check_output(cmd, shell=True,
                                              stderr=subprocess.STDOUT,
                                              encoding='utf8',
                                              env=os.environ) # TODO: hack for PATH
                import_count += 1
                logging.debug("- success")
            if opts.verbose:
                show_status(import_count, len(filenames), " ({} skips) ".format(skip_count))

        except subprocess.CalledProcessError as e:
            if e.returncode == 1 and "not overwriting existing" in e.output:
                logging.debug("- skipping existing file.")
                skip_count += 1
            else:
                logging.error("error in import: {code}\noutput:\n{output}".format(code=e.returncode, output=e.output))
                logging.info("stopping import.")
                return False


def add_metadata_to_imported_file(m):
    addmdcmd = "git -c annex.alwayscommit=false annex metadata \"{fname}\" {kvstr} --quiet"
    kvstr = ""

    for k,v in m.items():

        if k not in WANTED_KEYS:
            continue

        key = str(k.split(" ")[-1])
        value = str(v)
        if key == "SourceFile":
            value = os.path.basename(v)

        kvstr += " -s " + quote("{key}={value}".format(key=key, value=value))

    try:
        fn = m["filename_for_git_annex"]
        cmd = addmdcmd.format(fname=fn, kvstr=kvstr)
        logging.debug("\t - " + cmd)
        out = subprocess.check_output(cmd, shell=True,
                                      stderr=subprocess.STDOUT,
                                      env=os.environ) # TODO: hack for PATH
    except subprocess.CalledProcessError as e:
        logging.error("error in add_metadata_to_imported_file:"
              "{code}\noutput:\n{output}".format(code=e.returncode, output=e.output))
        return False

# NOTE: DmsToDecimal and GetGps are from
# https://developers.google.com/kml/articles/geotagsimple, and as per
# that page are licensed as Apache 2.0 License.
def DmsToDecimal(degree_num, degree_den, minute_num, minute_den,
                 second_num, second_den):
  """Converts the Degree/Minute/Second formatted GPS data to decimal degrees.

  Args:
    degree_num: The numerator of the degree object.
    degree_den: The denominator of the degree object.
    minute_num: The numerator of the minute object.
    minute_den: The denominator of the minute object.
    second_num: The numerator of the second object.
    second_den: The denominator of the second object.

  Returns:
    A deciminal degree.
  """

  degree = float(degree_num)/float(degree_den)
  minute = float(minute_num)/float(minute_den)/60
  second = float(second_num)/float(second_den)/3600
  return degree + minute + second


def GetGps(data):
  """Parses out the GPS coordinates from the file.

  Args:
    data: A dict object representing the Exif headers of the photo.

  Returns:
    A tuple representing the latitude, longitude, and altitude of the photo.
  """
  if ('GPS GPSLatitude' not in data or
      'GPS GPSLongitude' not in data):
      return None

  lat_dms = data['GPS GPSLatitude'].values
  long_dms = data['GPS GPSLongitude'].values
  latitude = DmsToDecimal(lat_dms[0].num, lat_dms[0].den,
                          lat_dms[1].num, lat_dms[1].den,
                          lat_dms[2].num, lat_dms[2].den)
  longitude = DmsToDecimal(long_dms[0].num, long_dms[0].den,
                           long_dms[1].num, long_dms[1].den,
                           long_dms[2].num, long_dms[2].den)
  if data['GPS GPSLatitudeRef'].printable == 'S': latitude *= -1
  if data['GPS GPSLongitudeRef'].printable == 'W': longitude *= -1
  altitude = None

  try:
    alt = data['GPS GPSAltitude'].values[0]
    altitude = alt.num/alt.den
    if data['GPS GPSAltitudeRef'] == 1: altitude *= -1

  except KeyError:
    altitude = 0

  return latitude, longitude, altitude

UNKNOWN_PLACE_DICT = {"Formatted Address": "unknown",
                      "County": "unknown",
                      "State": "unknown",
                      "Country": "unknown",
                      "Locality": "unknown",
                      "Neighborhood": "unknown",
                      "Postal Code": "unknown",
                      "Route": "unknown"}

def place_info_from_metadata(m):
    gps = GetGps(m)
    if gps is None:
        logging.debug("no lat, lng for file {}, using 'unknown'".format(m["SourceFile"]))
        return UNKNOWN_PLACE_DICT

    lat, lng, alt = gps
    if "unknown" in [lat, lng]:
        logging.debug("no lat, lng for file {}, using 'unknown'".format(m["SourceFile"]))
        return UNKNOWN_PLACE_DICT

    ut = "http://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lng}&sensor=false"
    url = ut.format(lat=lat, lng=lng)

    logging.info("\nRequest: " + url)
    response = urlopen(url)
    data = json.load(response)

    if data['status'] != 'OK':
        logging.info("error in geocoding: " + str(data))
        return {}

    # for now, just get most specific result and use its address components:
    d = data['results'][0]

    m = {}
    m["Formatted Address"] = d["formatted_address"]

    # super hacky and US-centric:
    actypemap = {"administrative_area_level_2": "County",
                 "administrative_area_level_1": "State",
                 "country": "Country",
                 "locality": "Locality",
                 "neighborhood": "Neighborhood",
                 "postal_code": "Postal Code",
                 "route": "Route"}

    for ac in d['address_components']:
        actype = ac['types'][0]
        if actype in actypemap:
            m[actypemap[actype]] = ac["long_name"]

    return m


def get_metadata_using_exiftool(filenames):
    filenames = " ".join(filenames)
    jstr = subprocess.check_output("exiftool -json {}".format(quote(filenames)), shell=True)
    raw_mlist = json.loads(jstr)
    mlist = [defaultdict(lambda: "unknown", **m_raw) for m_raw in raw_mlist]
    return mlist

def get_metadata_using_exifread(filenames, opts):
    mlist = []
    files_processed = 0
    for fn in filenames:
        with open(fn, 'rb') as f:
            # details = false to avoid parsing thumbs for now:
            m = exifread.process_file(f, 'unknown', details=False)
            m["SourceFile"] = os.path.abspath(fn)
            mlist.append(m)
            files_processed += 1
            if opts.verbose:
                show_status(files_processed, len(filenames), " ({}) ".format(fn))
    return mlist


def expand_filenames(opts, fnargs):
    "recurse"
    r = []
    for arg in fnargs:

        arg = os.path.abspath(arg)

        if os.path.basename(arg).startswith("."):
            logging.info("ignoring dotfile:" + arg)
            continue

        if os.path.isdir(arg):
            if opts.recursive:
                abs_list = [os.path.join(arg, a) for a in os.listdir(arg)]
                r += expand_filenames(opts, abs_list)
            else:
                logging.info("ignoring directory " + arg)
        else:
            r.append(os.path.abspath(arg))

    return r


def main(opts):
    logging.info("import.py started {}".format(time.asctime()))

    fnargs = expand_filenames(opts, opts.fnargs)

    os.chdir(opts.annex)

    if USE_STAGING:
        staging_dir = os.getenv("STAGING_DIR")
        staging_dir = os.path.abspath(staging_dir) if staging_dir else None
        if not staging_dir:
            staging_dir = tempfile.mkdtemp("git-annex-import", dir="/tmp")
        if not os.path.exists(staging_dir):
            logging.info("creating staging dir '{}'".format(staging_dir))
            os.mkdir(staging_dir)
        else:
            logging.info("using existing staging dir '{}'".format(staging_dir))
    else:
        staging_dir = ""

    files_to_import = []


    if opts.dryrun:
        pprint.pprint("filenames to import: ")
        pprint.pprint(fnargs)
        print("number of filenames: {}".format(len(fnargs)))
        sys.exit()

    logging.info("Received {} filenames as arguments. Getting metadata.".format(len(fnargs)))
    #mlist = get_metadata_using_exiftool(fnargs)
    mlist = get_metadata_using_exifread(fnargs, opts)
    logging.info("\nGot metadata for {} filenames.".format(len(mlist)))

    for m in mlist:
        try:
            m["filename_for_git_annex"] = filename_from_metadata(m)
        except Exception as e:
            source_file_name = m['SourceFile']
            logging.exception("error getting filename from metadata for source_file_name={}".format(source_file_name))
            pprint.pprint(m)
            raise e

    moved_count = 0
    for m in mlist:
        source_file_name = m['SourceFile']
        # print("metadata from sourcefile {} :".format(source_file_name))
        # pprint.pprint(m)

        filename_for_git_annex = os.path.join(staging_dir, m["filename_for_git_annex"])

        if USE_STAGING:
            infostr = "copying {} to {} ".format(source_file_name, filename_for_git_annex)
            logging.debug(infostr)
            shutil.copy2(source_file_name, filename_for_git_annex)
        else:
            infostr = "moving {} to {} ".format(source_file_name, filename_for_git_annex)
            logging.debug(infostr)
            if os.path.abspath(source_file_name) != os.path.abspath(filename_for_git_annex) \
            and os.path.islink(source_file_name): # Already imported: symlink to annex object
                subprocess.check_output(["git", "mv", source_file_name, filename_for_git_annex])
            else:
                shutil.move(source_file_name, filename_for_git_annex)

        moved_count += 1
        if opts.verbose:
            show_status(moved_count, len(mlist), infostr)
        files_to_import.append(filename_for_git_annex)

    logging.info("\nAbout to import {} files".format(len(files_to_import)))
    success = import_files(files_to_import)
    if success == False:
        # todo: remove temp dir?
        logging.error("errors importing files. exiting.")
        sys.exit()

    logging.info("\nUpdating metadata")
    updated_count = 0
    for m in mlist:
        ts = timestruct_from_metadata(m, m["SourceFile"] if USE_STAGING else m["filename_for_git_annex"])

        m.update(dict(Year=ts.tm_year,
                  Month=ts.tm_mon,
                  Day=ts.tm_mday))

        if not os.getenv("SKIP_PLACE_INFO", False):
            m.update(place_info_from_metadata(m))

        add_metadata_to_imported_file(m)
        updated_count += 1

        if opts.verbose:
            show_status(updated_count, len(mlist), "({}) ".format(m['SourceFile']))


    if staging_dir != "":
        logging.debug("removing {}".format(staging_dir))
        shutil.rmtree(staging_dir)

    logging.info("\nDone!")

#TODO - commit at end
# TODO: exifread doesn't do MOV :(

if __name__ == '__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument('annex',
                        help='destination git-annex directory')
    # parser.add_argument('--staging-dir',
    #                     help='directory to copy to before importing.'
    #                     ' (default is no copy, moving sources immediately)')
    parser.add_argument('-r', dest='recursive', action='store_true',
                        help='import all files from subdirectories')
    parser.add_argument('-v', dest='verbose', action='store_true',
                        help='echo logging to stderr')
    parser.add_argument('--dryrun', action='store_true',
                        help='show actions that would be performed')
    parser.add_argument('fnargs', nargs=argparse.REMAINDER,
                        help='file names and directories to import from')
    opts = parser.parse_args()

    setup_logging(opts.verbose)

    main(opts)
