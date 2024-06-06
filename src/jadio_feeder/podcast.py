from __future__ import annotations

import copy
import datetime as dt
import os
import urllib
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from logging import getLogger
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

import feedgen.entry
import feedgen.feed
import pytz
from bson import ObjectId
from jadio import Program
from mutagen import mp3, mp4
from serdescontainer import BaseContainer

PathLike = Union[str, Path]

RADIKO_LINK = "https://radiko.jp/"

logger = getLogger(__name__)


def _media_path_to_duration(path: Union[str, Path]) -> int:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} is not found")
    if ".mp3" == path.suffix:
        media = mp3.MP3(path)
    elif ".m4a" == path.suffix:
        # NOTE: M4A: Implementation removed. Every operation will raise. Use mp4 instead.
        # https://mutagen.readthedocs.io/en/latest/changelog.html#id28
        # media = m4a.M4A(path)
        media = mp4.MP4(path)
    elif ".mp4" == path.suffix:
        media = mp4.MP4(path)
    else:
        raise ValueError(f"{path.suffix} is not supported file type")
    return media.info.length


def _datetime_to_pub_data(datetime: dt.datetime, zone: str = "Asia/Tokyo") -> str:
    datetime = copy.deepcopy(datetime)
    datetime = datetime.replace(tzinfo=pytz.timezone(zone))
    return datetime.strftime("%a, %d %b %Y %H:%M:%S %z")


def _path_to_enclosure_url(path: Path, path_root: Path, base_url: str) -> str:
    url = os.path.relpath(str(path.absolute()), str(path_root))
    url = urllib.parse.quote(url)
    # TODO: fix join method
    return urllib.parse.urljoin(base_url, "media/" + url)


def _path_to_enclosure_length(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(f"{path} is not found")
    return os.path.getsize(str(path.absolute()))


def _path_to_enclosure_type(path: Path, is_video: bool) -> str:
    if ".mp3" == path.suffix:
        return "audio/mpeg"
    elif ".m4a" == path.suffix:
        return "audio/x-m4a"
    elif ".mp4" == path.suffix:
        return "video/mp4" if is_video else "audio/x-m4a"
    elif ".mov" == path.suffix:
        return "video/quicktime"
    else:
        raise ValueError(f"{path} is unsupported file type")


@dataclass
class Enclosure:
    url: str
    length: Optional[int] = None  # file size in bytes
    type: Optional[str] = None

    @classmethod
    def from_path(
        cls,
        path: Union[str, Path],
        is_video: bool,
        base_url: str,
        media_root: Path,
    ) -> Enclosure:
        path = Path(path)
        return cls(
            url=_path_to_enclosure_url(path, media_root, base_url=base_url),
            length=_path_to_enclosure_length(path),
            type=_path_to_enclosure_type(path, is_video),
        )


class EpisodeType(Enum):
    FULL: str = "full"
    TRAILER: str = "trailer"
    BONUS: str = "bonus"


@dataclass
class ItunesCategory(BaseContainer):
    cat: Optional[str] = None
    sub: Optional[str] = None


class ItunesType(Enum):
    EPISODIC: str = "episodic"
    SERIAL: str = "serial"


@dataclass
class PodcastItem(BaseContainer):
    """
    See: https://help.apple.com/itc/podcasts_connect/#/itcb54353390
    """

    # Required tags
    title: str
    enclosure: Enclosure
    guid: str

    # Recommended tags
    pub_date: Optional[dt.datetime] = None
    description: Optional[str] = None
    itunes_duration: Optional[int] = None  # [seconds]
    link: Optional[str] = None
    itunes_image: Optional[str] = None
    itunes_explicit: bool = False

    # Situational tags
    itunes_title: Optional[str] = None
    itunes_episode: Optional[int] = None
    itunes_season: Optional[int] = None
    itunes_episode_type: EpisodeType = EpisodeType.FULL
    podcast_transcript: Optional[str] = None
    itunes_block: bool = False

    def custom_types() -> List[Any]:
        """For BaseContainer.from_dict"""
        return [Enclosure, EpisodeType]

    @classmethod
    def from_program(
        cls,
        program: Program,
        program_id: ObjectId,
        base_url: str,
        media_root: Path,
    ) -> PodcastItem:
        media_dir = media_root.joinpath(
            program.platform_id, program.station_id, str(program_id)
        )
        media_path = list((media_dir).glob("media.*"))[0]
        duration = program.duration or _media_path_to_duration(media_path)
        return cls(
            title=program.episode_name,
            enclosure=Enclosure.from_path(
                media_path, program.is_video, base_url, media_root
            ),
            guid=str(program.episode_id),
            pub_date=program.datetime,
            description=program.description or program.information,
            itunes_duration=int(duration),
            link=program.url,
            itunes_image=program.image_url,
        )

    def set_feed_entry(self, entry: feedgen.entry.FeedEntry) -> None:
        entry.title(self.title)
        entry.enclosure(
            url=self.enclosure.url,
            length=self.enclosure.length,
            type=self.enclosure.type,
        )
        entry.guid(self.guid, permalink=False)

        if self.pub_date:
            entry.pubDate(_datetime_to_pub_data(self.pub_date))
        entry.description(self.description)
        entry.podcast.itunes_duration(self.itunes_duration)
        entry.link(href=self.link or RADIKO_LINK)
        # WORKAROUND: avoid file format error
        entry.podcast._PodcastEntryExtension__itunes_image = self.itunes_image
        entry.podcast.itunes_explicit("yes" if self.itunes_explicit else "no")

        entry.podcast.itunes_title(self.itunes_title)
        entry.podcast.itunes_episode(self.itunes_episode)
        entry.podcast.itunes_season(self.itunes_season)
        entry.podcast.itunes_episode_type(self.itunes_episode_type.value)
        # entry.podcast.itunes_transcript(self.podcast_transcript)
        entry.podcast.itunes_block(self.itunes_block)


@dataclass
class PodcastChannel(BaseContainer):
    # Required tags
    title: str
    description: str
    itunes_image: Optional[str] = None
    language: str = "ja"
    itunes_category: Optional[ItunesCategory] = None
    itunes_explicit: bool = False

    # Recommended tags
    itunes_author: Optional[str] = None
    link: Optional[str] = None

    # Situational tags
    itunes_title: Optional[str] = None
    itunes_type: ItunesType = ItunesType.EPISODIC
    copyright: Optional[str] = None
    itunes_new_feed_url: Optional[str] = None
    itunes_block: bool = False
    itunes_complete: bool = False

    def custom_types() -> List[Any]:
        """For BaseContainer.from_dict"""
        return [ItunesCategory, ItunesType]

    @classmethod
    def from_program(cls, program: Program) -> PodcastChannel:
        return PodcastChannel(
            title=program.name,
            description=program.description or program.information,
            itunes_image=program.image_url,
            itunes_author=program.station_id,
            link=program.url,
            copyright=program.copyright,
        )

    def to_feed_generator(self) -> feedgen.feed.FeedGenerator:
        ret = feedgen.feed.FeedGenerator()
        ret.load_extension("podcast")

        ret.title(self.title)
        ret.description(self.description)
        # WORKAROUND: avoid file format error
        ret.podcast._PodcastExtension__itunes_image = self.itunes_image
        ret.language(self.language)
        if self.itunes_category:
            ret.podcast.itunes_category(self.itunes_category.to_dict())
        ret.podcast.itunes_explicit("yes" if self.itunes_explicit else "no")

        ret.podcast.itunes_author(self.itunes_author)
        ret.link(href=self.link or RADIKO_LINK)

        # ret.podcast.itunes_title(self.itunes_title)
        ret.podcast.itunes_type(self.itunes_type.value)
        ret.copyright(self.copyright)
        ret.podcast.itunes_new_feed_url(self.itunes_new_feed_url)
        ret.podcast.itunes_block(self.itunes_block)
        ret.podcast.itunes_complete(self.itunes_complete)
        return ret


class PodcastRssFeedGenCreator:
    def __init__(
        self,
        base_url: str,
        media_root: PathLike,
    ) -> None:
        self.base_url = base_url
        self.media_root = Path(media_root)

    def create(
        self,
        program_and_id_pairs: List[Tuple[Program, ObjectId]],
        channel: Optional[PodcastChannel] = None,
        sort_by: Optional[str] = None,
        from_oldest: bool = False,
        remove_duplicates: bool = True,
    ) -> feedgen.feed.FeedGenerator:
        if sort_by is not None:
            available_sort_by = ["datetime", "episode_id"]
            if sort_by not in available_sort_by:
                raise ValueError(
                    f"'{sort_by}' is not supported sort_by. "
                    "Please select 'datetime' or 'eposode_id'"
                )
        elif len(set(program.station_id for program, _ in program_and_id_pairs)) > 1:
            # do not sort by episode_id because multiple platforms may be mixed
            sort_by = "datetime"
        elif program_and_id_pairs[0][0].station_id in ["onsen.ag", "hibiki-radio.jp"]:
            # if station_id is unified with onsen.ag or hibiki-radio.jp,
            # it is best to sort by episode_id.
            sort_by = "episode_id"
        else:
            # if station_id is unified with stations of radiko.jp,
            # it is best to sort by datetime.
            sort_by = "datetime"
        program_and_id_pairs = sorted(
            program_and_id_pairs,
            key=lambda x: getattr(x[0], sort_by),
            reverse=not from_oldest,
        )

        if remove_duplicates:
            unique_pairs = []
            prev_datetime = None
            prev_episode_id = None
            for program, program_id in program_and_id_pairs:
                if (
                    prev_datetime != program.datetime
                    and prev_episode_id != program.episode_id
                ):
                    unique_pairs.append((program, program_id))
                prev_datetime = program.datetime
                prev_episode_id = program.episode_id
            num_programs = len(program_and_id_pairs)
            if num_programs != len(unique_pairs):
                logger.info(
                    f"found and removed {num_programs - len(unique_pairs)} duplicates"
                )
            program_and_id_pairs = unique_pairs

        # create channel of RSS feed
        if not channel:
            latest_program = program_and_id_pairs[-1 if from_oldest else 0][0]
            channel = PodcastChannel.from_program(latest_program)

        # create items of RSS feed
        feed_generator = channel.to_feed_generator()
        for program, program_id in program_and_id_pairs:
            try:
                item = PodcastItem.from_program(
                    program, program_id, self.base_url, self.media_root
                )
                # item order has been already controled
                item.set_feed_entry(feed_generator.add_entry(order="append"))
            except Exception as err:
                logger.error(f"error: {err}\n{program}", stack_info=True)
                raise err

        return feed_generator
