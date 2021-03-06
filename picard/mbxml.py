# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
# Copyright (C) 2007 Lukáš Lalinský
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import re
import unicodedata
from picard.util import format_time, translate_from_sortname
from picard.const import RELEASE_FORMATS


AMAZON_ASIN_URL_REGEX = re.compile(r'^http://(?:www.)?(.*?)(?:\:[0-9]+)?/.*/([0-9B][0-9A-Z]{9})(?:[^0-9A-Z]|$)')

_artist_rel_types = {
    "composer": "composer",
    "writer": "writer",
    "conductor": "conductor",
    "chorus master": "conductor",
    "performing orchestra": "performer:orchestra",
    "arranger": "arranger",
    "orchestrator": "arranger",
    "instrumentator": "arranger",
    "lyricist": "lyricist",
    "librettist": "lyricist",
    "remixer": "remixer",
    "producer": "producer",
    "engineer": "engineer",
    "audio": "engineer",
    #"Mastering": "engineer",
    "sound": "engineer",
    "live sound": "engineer",
    "mix": "mixer",
    #"Recording": "engineer",
    "mix-DJ": "djmixer",
}


def _decamelcase(text):
    return re.sub(r'([A-Z])', r' \1', text).strip()


_REPLACE_MAP = {}
_EXTRA_ATTRS = ['guest', 'additional', 'minor']
def _parse_attributes(attrs):
    attrs = [_decamelcase(_REPLACE_MAP.get(a, a)) for a in attrs]
    prefix = ' '.join([a for a in attrs if a in _EXTRA_ATTRS])
    attrs = [a for a in attrs if a not in _EXTRA_ATTRS]
    if len(attrs) > 1:
        attrs = '%s and %s' % (', '.join(attrs[:-1]), attrs[-1:][0])
    elif len(attrs) == 1:
        attrs = attrs[0]
    else:
        attrs = ''
    return ' '.join([prefix, attrs]).strip().lower()


def _relations_to_metadata(relation_lists, m, config):
    for relation_list in relation_lists:
        if relation_list.target_type == 'artist':
            for relation in relation_list.relation:
                artist = relation.artist[0]
                value = _translate_artist_node(artist, config) or artist.name[0].text
                reltype = relation.type
                attribs = []
                if 'attribute_list' in relation.children:
                    attribs = [a.text for a in relation.attribute_list[0].attribute]
                if reltype == 'vocal':
                    name = 'performer:' + ' '.join([_parse_attributes(attribs), 'vocal']).strip()
                elif reltype == 'instrument':
                    name = 'performer:' + _parse_attributes(attribs)
                elif reltype == 'performer':
                    name = 'performer:' + _parse_attributes(attribs)
                elif reltype == 'mix-DJ' and len(attribs) > 0:
                    if not hasattr(m, "_djmix_ars"):
                        m._djmix_ars = {}
                    for attr in attribs:
                        m._djmix_ars.setdefault(attr.split()[1], []).append(value)
                    continue
                else:
                    try:
                        name = _artist_rel_types[reltype]
                    except KeyError:
                        continue
                if value not in m[name]:
                    m.add(name, value)
        elif relation_list.target_type == 'work':
            for relation in relation_list.relation:
                if relation.type == 'performance':
                    work_to_metadata(relation.work[0], m, config)
        elif relation_list.target_type == 'url':
            for relation in relation_list.relation:
                if relation.type == 'amazon asin':
                    url = relation.target[0].text
                    match = AMAZON_ASIN_URL_REGEX.match(url)
                    if match is not None and 'asin' not in m:
                        m['asin'] = match.group(2)
                if relation.type == 'license':
                    url = relation.target[0].text
                    m.add('license', url)


def _translate_artist_node(node, config=None):
    transl = None
    if config and config.setting['translate_artist_names']:
        locale = config.setting["artist_locale"]
        lang = locale.split("_")[0]
        if "alias_list" in node.children:
            for alias in node.alias_list[0].alias:
                if "locale" in alias.attribs:
                    if alias.locale == locale:
                        return alias.text
                    elif alias.locale == lang:
                        transl = alias.text
        if lang == "en" and not transl:
            transl = translate_from_sortname(node.name[0].text, node.sort_name[0].text)
    return transl


def artist_credit_from_node(node, config=None):
    artist = ""
    artistsort = ""
    for credit in node.name_credit:
        a = credit.artist[0]
        artistsort += a.sort_name[0].text
        transl = _translate_artist_node(a, config)
        if transl:
            artist += transl
        else:
            if 'name' in credit.children and not (config and config.setting["standardize_artists"]):
                artist += credit.name[0].text
            else:
                artist += a.name[0].text
        if 'joinphrase' in credit.attribs:
            artist += credit.joinphrase
            artistsort += credit.joinphrase
    return (artist, artistsort)


def artist_credit_to_metadata(node, m, config, release=False):
    ids = [n.artist[0].id for n in node.name_credit]
    artist, artistsort = artist_credit_from_node(node, config)
    if release:
        m["musicbrainz_albumartistid"] = ids
        m["albumartist"] = artist
        m["albumartistsort"] = artistsort
    else:
        m["musicbrainz_artistid"] = ids
        m["artist"] = artist
        m["artistsort"] = artistsort


def label_info_from_node(node):
    labels = []
    catalog_numbers = []
    if node.count != "0":
        for label_info in node.label_info:
            if 'label' in label_info.children:
                labels.append(label_info.label[0].name[0].text)
            if 'catalog_number' in label_info.children:
                catalog_numbers.append(label_info.catalog_number[0].text)
    return (labels, catalog_numbers)


def media_formats_from_node(node):
    formats_count = {}
    formats_order = []
    for medium in node.medium:
        if "format" in medium.children:
            text = medium.format[0].text
        else:
            text = "(unknown)"
        if text in formats_count:
            formats_count[text] += 1
        else:
            formats_count[text] = 1
            formats_order.append(text)
    formats = []
    for format in formats_order:
        count = formats_count[format]
        format = RELEASE_FORMATS.get(format, format)
        if count > 1:
            format = str(count) + u"×" + format
        formats.append(format)
    return " + ".join(formats)


def track_to_metadata(node, track, config):
    m = track.metadata
    recording_to_metadata(node.recording[0], track, config)
    # overwrite with data we have on the track
    for name, nodes in node.children.iteritems():
        if not nodes:
            continue
        if name == 'title':
            m['title'] = nodes[0].text
        elif name == 'position':
            m['tracknumber'] = nodes[0].text
        elif name == 'length' and nodes[0].text:
            m.length = int(nodes[0].text)
        elif name == 'artist_credit':
            artist_credit_to_metadata(nodes[0], m, config)
    m['~length'] = format_time(m.length)


def recording_to_metadata(node, track, config):
    m = track.metadata
    m.length = 0
    m['musicbrainz_trackid'] = node.attribs['id']
    for name, nodes in node.children.iteritems():
        if not nodes:
            continue
        if name == 'title':
            m['title'] = nodes[0].text
        elif name == 'length' and nodes[0].text:
            m.length = int(nodes[0].text)
        elif name == 'disambiguation':
            m['~recordingcomment'] = nodes[0].text
        elif name == 'artist_credit':
            artist_credit_to_metadata(nodes[0], m, config)
        elif name == 'relation_list':
            _relations_to_metadata(nodes, m, config)
        elif name == 'tag_list':
            add_folksonomy_tags(nodes[0], track)
        elif name == 'user_tag_list':
            add_user_folksonomy_tags(nodes[0], track)
        elif name == 'isrc_list':
            add_isrcs_to_metadata(nodes[0], m)
        elif name == 'user_rating':
            m['~rating'] = nodes[0].text
    m['~length'] = format_time(m.length)

def work_to_metadata(work, m, config):
    m.add("musicbrainz_workid", work.attribs['id'])
    if 'relation_list' in work.children:
        _relations_to_metadata(work.relation_list, m, config)

def medium_to_metadata(node, m):
    for name, nodes in node.children.iteritems():
        if not nodes:
            continue
        if name == 'position':
            m['discnumber'] = nodes[0].text
        elif name == 'track_list':
            m['totaltracks'] = nodes[0].count
        elif name == 'title':
            m['discsubtitle'] = nodes[0].text
        elif name == 'format':
            m['media'] = nodes[0].text


def release_to_metadata(node, m, config, album=None):
    """Make metadata dict from a XML 'release' node."""
    m['musicbrainz_albumid'] = node.attribs['id']
    for name, nodes in node.children.iteritems():
        if not nodes:
            continue
        if name == 'release_group':
            release_group_to_metadata(nodes[0], m, config, album)
        elif name == 'status':
            m['releasestatus'] = nodes[0].text.lower()
        elif name == 'title':
            m['album'] = nodes[0].text
        elif name == 'disambiguation':
            m['~releasecomment'] = nodes[0].text
        elif name == 'asin':
            m['asin'] = nodes[0].text
        elif name == 'artist_credit':
            artist_credit_to_metadata(nodes[0], m, config, release=True)
        elif name == 'date':
            m['date'] = nodes[0].text
        elif name == 'country':
            m['releasecountry'] = nodes[0].text
        elif name == 'barcode':
            m['barcode'] = nodes[0].text
        elif name == 'relation_list':
            _relations_to_metadata(nodes, m, config)
        elif name == 'label_info_list' and nodes[0].count != '0':
            m['label'], m['catalognumber'] = label_info_from_node(nodes[0])
        elif name == 'text_representation':
            if 'language' in nodes[0].children:
                m['language'] = nodes[0].language[0].text
            if 'script' in nodes[0].children:
                m['script'] = nodes[0].script[0].text
        elif name == 'tag_list':
            add_folksonomy_tags(nodes[0], album)
        elif name == 'user_tag_list':
            add_user_folksonomy_tags(nodes[0], album)


def release_group_to_metadata(node, m, config, album=None):
    """Make metadata dict from a XML 'release-group' node taken from inside a 'release' node."""
    if 'type' in node.attribs:
        m['releasetype'] = node.type.lower()
    for name, nodes in node.children.iteritems():
        if not nodes:
            continue
        if name == 'first_release_date':
            m['originaldate'] = nodes[0].text
        elif name == 'tag_list':
            add_folksonomy_tags(nodes[0], album)
        elif name == 'user_tag_list':
            add_user_folksonomy_tags(nodes[0], album)


def add_folksonomy_tags(node, obj):
    if obj and 'tag' in node.children:
        for tag in node.tag:
            name = tag.name[0].text
            count = int(tag.attribs['count'])
            obj.add_folksonomy_tag(name, count)


def add_user_folksonomy_tags(node, obj):
    if obj and 'user_tag' in node.children:
        for tag in node.user_tag:
            name = tag.name[0].text
            obj.add_folksonomy_tag(name, 1)


def add_isrcs_to_metadata(node, metadata):
    if 'isrc' in node.children:
        for isrc in node.isrc:
            metadata.add('isrc', isrc.id)
