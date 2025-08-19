#!/usr/bin/env python3

from __future__ import annotations

import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from collections.abc import Iterator
from collections import defaultdict
import json
from pathlib import Path
import argparse
import itertools
import sys
import dominate  # type: ignore
from dominate.tags import span, li, div, ol, a, style  # type: ignore

# https://developers.google.com/youtube/v3/docs/comments#resource-representation
COMMENT_FIELDS = "id,snippet(parentId,authorDisplayName,authorChannelId/value,textDisplay,publishedAt)"
SECONDS_IN_MINUTE = 60
SECONDS_IN_HOUR = 3600
SECONDS_IN_DAY = 86400
SECONDS_IN_MONTH = SECONDS_IN_DAY * 30
SECONDS_IN_THREE_MONTHS = SECONDS_IN_DAY * 90
SECONDS_IN_YEAR = SECONDS_IN_DAY * 365

STYLES = """
body {
  font-family:Verdana,Geneva,sans-serif;
  font-size:9pt;
  color:#000000;
  background-color:#f6f6ef;
}
.maincontent {
  display:flex;
  flex-direction:column;
  justify-content:center;
  align-items:center;
}
.maincontent div.threadcontainer { width:50%; }
.maincontent div.titlesection {
  width:50%;
  padding-block-start:1em;
  padding-block-end:1em;
}
.titlesection {
  font-family:Verdana,Geneva,sans-serif;
  font-size:10pt;
  color:#828282;
  border-top:0.2rem dashed #ff6600;
  margin-block-start: 0.5em;
}
.titlesection a:link, .titlesection a:visited { color:#000000; text-decoration:none; }
.titlenav { font-family:Verdana,Geneva,sans-serif; font-size:8pt; color:#828282; }
.titlenav a:link, .titlenav a:visited { color:#828282; text-decoration:none; }
.titlenav a:hover { text-decoration:underline; }
ol.threadrootlevel { padding-left:0; }
ol { margin-block-start:0em; margin-block-end:0em; }
li { list-style-type:none; }
.commentheader { font-family:Verdana,Geneva,sans-serif; font-size:8pt; color:#828282; }
.commentheader a:link, .commentheader a:visited { color:#828282; text-decoration:none; }
.commentheader a:hover { text-decoration:underline; }
.commenttext { margin-block-start: 0.3em; margin-block-end: 1.5em; }
.commenttext a {
  background:#fbe1cb;
  border:0.1rem dashed #828282;
  border-radius:0.25em;
  color:#000000;
  padding:0rem 0.2rem;
  text-decoration:none;
  display:inline-block;
  margin-bottom:0.3em;
}
"""


@dataclass
class Comment:
    comment_id: str
    thread_id: str
    parent_id: str | None
    video_id: str
    author_handle: str
    author_channel_id: str
    text: str
    published_at: datetime

    def __str__(self) -> str:
        return f"{self.author_handle}: {self.text}"

    @staticmethod
    def from_api(video_id: str, thread_id: str, data: dict[str, Any]) -> Comment:
        """
        JSON structure of `data`:
        https://developers.google.com/youtube/v3/docs/comments#resource-representation
        """
        snippet = data["snippet"]
        return Comment(
            comment_id=data["id"],
            thread_id=thread_id,
            parent_id=snippet.get("parentId", None),
            video_id=video_id,
            author_handle=snippet["authorDisplayName"],
            author_channel_id=snippet["authorChannelId"]["value"],
            text=snippet["textDisplay"],
            published_at=datetime.fromisoformat(snippet["publishedAt"]),
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Comment:
        return Comment(
            comment_id=data["comment_id"],
            thread_id=data["thread_id"],
            parent_id=data["parent_id"],
            video_id=data["video_id"],
            author_handle=data["author_handle"],
            author_channel_id=data["author_channel_id"],
            text=data["text"],
            published_at=datetime.fromisoformat(data["published_at"]),
        )

    @property
    def timestamp(self) -> str:
        return self.published_at.strftime("%Y %b %d %H:%M:%S")

    @property
    def author_and_timestamp(self) -> str:
        return f"{self.author_handle} {self.timestamp}"

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}&lc={self.comment_id}"

    @property
    def author_channel_url(self) -> str:
        return f"https://www.youtube.com/{self.author_handle}"

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "thread_id": self.thread_id,
            "parent_id": self.parent_id,
            "video_id": self.video_id,
            "author_handle": self.author_handle,
            "author_channel_id": self.author_channel_id,
            "text": self.text,
            "published_at": self.published_at.isoformat(),
        }

    def to_html(self, reference_time: datetime) -> li:
        with li() as elem:
            with span(cls="commentheader"):
                a(self.author_handle, href=self.author_channel_url, target="_blank")
                if readable := readable_time_delta(
                    older=self.published_at, newer=reference_time
                ):
                    ts_string = f"{readable} ago"
                else:
                    ts_string = f"on {self.timestamp}"
                with span(title=self.timestamp):
                    a(ts_string, href=self.youtube_url, target="_blank")
                dominate.util.text(" | ")
                a("up", href=f"#{self.video_id}")
            div(dominate.util.raw(self.text), cls="commenttext")
        return elem


@dataclass
class CommentThread:
    thread_id: str
    etag: str
    top_level_comment: Comment
    replies: list[Comment]

    def __iter__(self) -> Iterator[Comment]:
        yield self.top_level_comment
        yield from self.replies

    def __str__(self) -> str:
        return "".join(self.lines)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CommentThread:
        return CommentThread(
            thread_id=data["thread_id"],
            etag=data["etag"],
            top_level_comment=Comment.from_dict(data["top_level_comment"]),
            replies=[Comment.from_dict(reply_dict) for reply_dict in data["replies"]],
        )

    @property
    def lines(self) -> Iterator[str]:
        yield from (
            self.youtube_url,
            self.top_level_comment.author_and_timestamp,
            self.top_level_comment.text,
            *itertools.chain.from_iterable(
                ("│", f"└── {reply.author_and_timestamp}", f"    {reply.text}")
                for reply in self.replies
            ),
        )

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}&lc={self.thread_id}"

    @property
    def video_id(self) -> str:
        return self.top_level_comment.video_id

    @property
    def latest_ts(self) -> datetime:
        if self.replies:
            return self.replies[-1].published_at
        return self.top_level_comment.published_at

    @property
    def as_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "etag": self.etag,
            "top_level_comment": self.top_level_comment.as_dict,
            "replies": [reply.as_dict for reply in self.replies],
        }

    def to_html(self, reference_time: datetime) -> div:
        with div(cls="threadcontainer") as top_level_element:
            with ol(cls="threadrootlevel"):
                self.top_level_comment.to_html(reference_time)
                if self.replies:
                    with ol():
                        for r in self.replies:
                            r.to_html(reference_time)
        return top_level_element


def readable_time_delta(older: datetime, newer: datetime) -> str | None:
    num_seconds = (newer - older).total_seconds()
    if num_seconds < SECONDS_IN_HOUR:
        num_minutes = int(num_seconds // SECONDS_IN_MINUTE)
        unit = "minute" if num_minutes == 1 else "minutes"
        return f"{num_minutes} {unit}"
    if num_seconds < SECONDS_IN_DAY:
        num_hours = int(num_seconds // SECONDS_IN_HOUR)
        unit = "hour" if num_hours == 1 else "hours"
        return f"{num_hours} {unit}"
    if num_seconds < SECONDS_IN_THREE_MONTHS:
        num_days = int(num_seconds // SECONDS_IN_DAY)
        unit = "day" if num_days == 1 else "days"
        return f"{num_days} {unit}"
    if num_seconds < SECONDS_IN_YEAR:
        num_months = int(num_seconds // SECONDS_IN_MONTH)
        unit = "month" if num_months == 1 else "months"
        return f"{num_months} {unit}"
    return None


def at_most_n_elements[T](it: Iterator[T], n: int) -> Iterator[T]:
    for _, element in zip(range(n), it):
        yield element


def to_youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def save(output_file: Path, threads: list[CommentThread]) -> None:
    with output_file.open(mode="w") as f:
        for thread in threads:
            json.dump(thread.as_dict, f)
            f.write("\n")


def load_comments(input: Path) -> dict[str, CommentThread]:
    with input.open() as f:
        return {
            thread.thread_id: thread
            for line in f
            if (thread := CommentThread.from_dict(json.loads(line)))
        }


class CommentDownloader:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def get_channel_id(
        self, session: requests.Session, youtube_channel_handle: str
    ) -> str:
        r = session.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "forHandle": youtube_channel_handle,
                "key": self.api_key,
                "part": "id",
            },
        )
        r.raise_for_status()
        channel_id: str = r.json()["items"][0]["id"]
        return channel_id

    def comment_threads(
        self, session: requests.Session, channel_id: str
    ) -> Iterator[CommentThread]:
        def num_comment_thread_replies(thread: dict[str, Any]) -> int:
            """
            `thread`: https://developers.google.com/youtube/v3/docs/commentThreads#resource
            """
            if (replies := thread.get("replies")) and (
                comments := replies.get("comments")
            ):
                return len(comments)
            return 0

        next_page_token = None
        while True:
            params: dict[str, Any] = {
                "allThreadsRelatedToChannelId": channel_id,
                "key": self.api_key,
                "part": "id,replies,snippet",
                "maxResults": 100,
                "fields": f"nextPageToken,items(id,etag,snippet(videoId,totalReplyCount,topLevelComment({COMMENT_FIELDS})),replies/comments({COMMENT_FIELDS}))",
            } | ({} if next_page_token is None else {"pageToken": next_page_token})
            r = session.get(
                "https://www.googleapis.com/youtube/v3/commentThreads", params=params
            )
            r.raise_for_status()
            json_response = r.json()
            for thread in json_response["items"]:
                video_id = thread["snippet"]["videoId"]
                thread_id = thread["id"]
                total_reply_count = thread["snippet"]["totalReplyCount"]
                top_level_comment = Comment.from_api(
                    video_id=video_id,
                    thread_id=thread_id,
                    data=thread["snippet"]["topLevelComment"],
                )

                if total_reply_count != num_comment_thread_replies(thread):
                    replies = list(
                        self.all_comment_replies(
                            session=session,
                            parent_comment_id=top_level_comment.comment_id,
                            video_id=video_id,
                            thread_id=thread_id,
                        )
                    )
                else:
                    replies = (
                        [
                            Comment.from_api(
                                video_id=video_id,
                                thread_id=thread_id,
                                data=reply_comment,
                            )
                            for reply_comment in thread["replies"]["comments"]
                        ]
                        if total_reply_count > 0
                        else []
                    )
                yield CommentThread(
                    thread_id=thread_id,
                    etag=thread["etag"],
                    top_level_comment=top_level_comment,
                    replies=replies,
                )
            next_page_token = json_response.get("nextPageToken")
            if next_page_token is None:
                break

    def all_comment_replies(
        self,
        session: requests.Session,
        parent_comment_id: str,
        video_id: str,
        thread_id: str,
    ) -> Iterator[Comment]:
        next_page_token = None
        while True:
            params: dict[str, Any] = {
                "parentId": parent_comment_id,
                "key": self.api_key,
                "part": "id,snippet",
                "maxResults": 100,
                "fields": f"nextPageToken,items({COMMENT_FIELDS})",
            } | ({} if next_page_token is None else {"pageToken": next_page_token})
            r = session.get(
                "https://www.googleapis.com/youtube/v3/comments",
                params=params,
            )
            r.raise_for_status()
            json_response = r.json()
            for reply_comment in json_response["items"]:
                yield Comment.from_api(
                    video_id=video_id, thread_id=thread_id, data=reply_comment
                )
            next_page_token = json_response.get("nextPageToken")
            if next_page_token is None:
                break

    def fetch_video_titles(
        self,
        session: requests.Session,
        video_ids: list[str],
        batch_size: int = 20,
    ) -> dict[str, str]:
        res: dict[str, str] = dict()
        for video_id_batch in itertools.batched(video_ids, n=batch_size):
            params: dict[str, Any] = {
                "key": self.api_key,
                "id": ",".join(video_id_batch),
                "part": "id,snippet",
                "fields": "items(id,snippet/title)",
            }
            r = session.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params=params,
            )
            r.raise_for_status()
            res |= {item["id"]: item["snippet"]["title"] for item in r.json()["items"]}
        return res

    def digest(
        self,
        channel_handle: str,
        limit: int | None = None,
    ) -> str:
        video_id_to_threads: dict[str, list[CommentThread]] = defaultdict(list)
        with requests.Session() as session:
            channel_id = self.get_channel_id(session, channel_handle)
            all_threads = self.comment_threads(session, channel_id)
            thread_stream = (
                at_most_n_elements(all_threads, limit)
                if limit is not None
                else all_threads
            )
            for thread in thread_stream:
                video_id_to_threads[thread.video_id].append(thread)

            video_id_to_title = self.fetch_video_titles(
                session, list(video_id_to_threads)
            )

        return self.to_html_doc(video_id_to_title, video_id_to_threads)

    def to_html_doc(
        self,
        video_id_to_title: dict[str, str],
        video_id_to_threads: dict[str, list[CommentThread]],
    ) -> str:
        def most_recent_time(
            threads: list[CommentThread],
        ) -> datetime:
            return max(t.latest_ts for t in threads)

        video_ids = [
            video_id
            for _, video_id in sorted(
                [
                    (most_recent_time(threads), video_id)
                    for video_id, threads in video_id_to_threads.items()
                ],
                reverse=True,
            )
        ]
        previous_video_ids: list[str | None] = [None] + video_ids[:-1]
        next_video_ids: list[str | None] = video_ids[1:] + [None]

        doc = dominate.document(title="Placeholder")
        reference_time = datetime.now(timezone.utc)

        with doc.head:
            style(STYLES)

        with doc:
            with div(cls="maincontent"):
                for maybe_prev_video_id, video_id, maybe_next_video_id in zip(
                    previous_video_ids, video_ids, next_video_ids
                ):
                    video_title = video_id_to_title[video_id]
                    with div(cls="titlesection", id=video_id):
                        a(video_title, href=to_youtube_url(video_id), target="_blank")
                        with span(cls="titlenav"):
                            if prev_video_id := maybe_prev_video_id:
                                dominate.util.text(" | ")
                                a("prev", href=f"#{prev_video_id}")
                            if next_video_id := maybe_next_video_id:
                                dominate.util.text(" | ")
                                a("next", href=f"#{next_video_id}")

                    for thread in sorted(
                        video_id_to_threads[video_id],
                        key=lambda t: t.latest_ts,
                        reverse=True,
                    ):
                        match thread:
                            case CommentThread():
                                thread.to_html(reference_time)
        html_output: str = doc.render()
        return html_output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="ytcommentdl")
    parser.add_argument("channel_handle", type=str, help="YouTube channel handle")
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="Path to file containing YouTube API key",
        default=Path(sys.argv[0]).resolve().with_name("api_key"),
    )

    parser.add_argument(
        "-o", "--output-html", type=Path, help="Path to output HTML file"
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        help="Maximum number of threads to fetch",
        default=500,
    )
    args = parser.parse_args()
    my_api_key = args.api_key_file.read_text().strip()
    downloader = CommentDownloader(my_api_key)
    downloader.digest(
        channel_handle=args.channel_handle,
        limit=args.limit,
    )
