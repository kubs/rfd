# RFD

[![PyPI version](https://badge.fury.io/py/rfd.svg)](https://badge.fury.io/py/rfd)
[![Dependabot](https://badgen.net/badge/Dependabot/enabled/green?icon=dependabot)](https://dependabot.com/)
[![Downloads](https://pepy.tech/badge/rfd)](https://pepy.tech/project/rfd)

<!-- BEGIN mktoc -->
- [Description](#description)
- [Motivation](#motivation)
- [Installation](#installation)
- [Usage](#usage)
  - [View Hot Deals](#view-hot-deals)
  - [View and Sort Hot Deals](#view-and-sort-hot-deals)
  - [Search](#search)
    - [Regex](#regex)
  - [View Posts](#view-posts)
  - [Interactive Mode](#interactive-mode)
  - [JSON Output](#json-output)
- [Shell Completion](#shell-completion)
  - [bash](#bash)
  - [zsh](#zsh)
<!-- END mktoc -->

## Description

This is a CLI utility that allows you to view [RedFlagDeals.com](https://forums.redflagdeals.com) on the command line.

![screenshot](https://user-images.githubusercontent.com/4519234/85969861-e10a4100-b996-11ea-9a31-6203322c60ee.png)

## Motivation

It is often faster to use a CLI than to load up a web page and navigate web elements. This tool can search for deals and sort them based on score and views. It is also able to load entire threads (without pagination) for additional analysis.

## Installation

### pip

```sh
pip3 install --user rfd
```

This can also be installed with [pipx](https://github.com/pypa/pipx).

### brew

If you have [brew](https://brew.sh):

```sh
brew install davegallant/public/rfd
```

## Usage

All commands open up in a [terminal pager](https://en.wikipedia.org/wiki/Terminal_pager).

```sh
Usage: rfd [OPTIONS] COMMAND [ARGS]...

  CLI for https://forums.redflagdeals.com

Options:
  -v, --version
  --help         Show this message and exit.

Commands:
  posts    Display all posts in a thread.
  search   Search deals based on a regular expression.
  threads  Displays threads in the forum. Defaults to hot deals.
```

### View Hot Deals

To view the threads on most popular sub-forum:

```sh
rfd threads
```

### View and Sort Hot Deals

```sh
rfd threads --sort-by score
```

To view and sort multiple pages, use `--pages`:

```sh
rfd threads --sort-by views --pages 10
```

### Search

```sh
rfd search 'pizza'
```

#### Regex

Regular expressions can be used for search.

```sh
rfd search '(coffee|starbucks)' --pages 10 --sort-by views
```

### View Posts

It's possible to view an entire post and all comments by running:

```sh
rfd posts https://forums.redflagdeals.com/kobo-vs-kindle-2396227/
```

This allows for easy grepping and searching for desired expressions.

### Interactive Mode

Instead of dumping to a pager, `--interactive` (`-i`) opens a browser with vim key bindings,
so threads and their posts can be read without leaving the keyboard:

```sh
rfd threads -i
rfd search 'coffee' -i --pages 10
```

| Key | Action |
| --- | --- |
| `j` / `k`, `down` / `up` | move down / up |
| `ctrl-d` / `ctrl-u` | half page down / up |
| `ctrl-f` / `ctrl-b` | full page down / up |
| `g` / `G` | first / last, or `<number>G` to jump to a line |
| `enter`, `o`, `l` | open the selected thread |
| `q`, `h`, `esc` | go back (quits from the thread list) |
| `Q` | quit |
| `d` / `D` | open the deal link, cleaned / exactly as the forum gave it |
| `O` | open the thread on redflagdeals.com |
| `y` / `Y` | yank the thread / deal url to the clipboard |
| `/`, then `n` / `N` | search, next / previous match |
| `s` then `s` / `v` / `t` / `n` | sort by score / views / title / forum order |
| `s` then `r` | reverse the current sort |
| `p` | load the next page of threads |
| `r` | reload from the first page |
| `?` | show the keys |

An opened thread shows its links under the title: the deal, and the thread's own
url on the forum. Threads without a *get deal at ...* button usually have the
deal as a link in the first post, and that is used instead.

Link text in posts is truncated by the forum to something like
`https://www.staples.ca/produ ... er-20-pack`, which cannot be copied or
followed, so the real url is put back into the text.

Tracking is taken off both: affiliate hops through Skimlinks,
LinkSynergy and Impact Radius are unwrapped back to the real destination, and
`utm_*`, `tag`, `ref`, `gclid` and friends are dropped. Params the page needs to
show the right thing, like an Amazon variant or a store id, are kept, and a
redirect that hides its destination server side is left alone rather than
guessed at. `D` opens the untouched url the forum handed out, for when a deal
needs the affiliate hop. The cleaned link is in `--output json` as `deal_url`,
and every post carries the links found in it as `links`.

Posts are fetched page by page in the background, so a long thread is scrollable
as soon as its first page arrives.

The forum api caps a request at 40 threads, so the list pages itself: scrolling
towards the bottom pulls in the next page (`p` fetches one immediately), and
`--pages` still controls how much is loaded up front. Sorting applies to
everything loaded so far and keeps the cursor on the thread you were looking at,
so paging and sorting can be mixed freely.

### JSON Output

All commands support JSON output.

For example:

```sh
rfd threads --output json
```

## Shell Completion

Shell completion can be enabled if using `bash` or `zsh`.

### bash

```sh
echo 'eval "$(_RFD_COMPLETE=source rfd)"' >> ~/.profile
```

### zsh

```sh
echo 'eval "$(_RFD_COMPLETE=source_zsh rfd)"' >> ~/.zshrc
```
