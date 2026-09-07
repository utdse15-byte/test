"""Append-only review files; no project status writes."""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import typer
from ..authoring.cli import _guard, _emit
from ..authoring.core import Request, load_json
from .core import (ReviewDocument, create_session, candidate_from_file, record_decision,
                    promote, report)

app=typer.Typer(no_args_is_help=True,help='本地候选比较、人工记录与草稿晋升，不自动选片。')


@app.command('create')
@_guard
def create_command(request: Path, candidate: list[Path] = typer.Option(...,'--candidate'),
                   output: Path = typer.Option(...,'--output'),
                   hash_only: bool = typer.Option(False,'--hash-only')):
    """为固定任务创建新审片场次。默认 ffprobe 实测；hash-only 明确不验解码。"""
    req=Request.model_validate(load_json(request))
    doc=create_session(req,[candidate_from_file(p,probe=not hash_only) for p in candidate])
    _emit(doc,output)


@app.command('verify')
@_guard
def verify_command(review: Path):
    doc=ReviewDocument.model_validate(load_json(review))
    _emit({'ok':True,'session_sha256':doc.session.session_sha256,
           'candidates':len(doc.session.candidates),'decisions':len(doc.decisions),
           'candidate_media_bytes_checked':False,'picture_lock_authorized':False})


@app.command('decide')
@_guard
def decide_command(review: Path, candidate: str = typer.Option(...,'--candidate'),
                   file: Path = typer.Option(...,'--file'),
                   verdict: str = typer.Option(...,'--verdict'),
                   reviewer: str = typer.Option(...,'--reviewer'),
                   notes: str = typer.Option(...,'--notes'),
                   human_confirmed: bool = typer.Option(False,'--human-confirmed'),
                   output: Path = typer.Option(...,'--output')):
    """核对实际候选文件并追加人工决定；输出必须用新文件名。"""
    doc=ReviewDocument.model_validate(load_json(review))
    _emit(record_decision(doc,candidate,file,verdict=verdict,reviewer=reviewer,
                         notes=notes,human_confirmed=human_confirmed),output)


@app.command('promote')
@_guard
def promote_command(review: Path, candidate: str = typer.Option(...,'--candidate'),
                    file: Path = typer.Option(...,'--file'),
                    resolution: Optional[str] = typer.Option(None,'--resolution'),
                    human_confirmed: bool = typer.Option(False,'--human-confirmed'),
                    output: Path = typer.Option(...,'--output')):
    """将已批准的实际草稿晋升为定稿任务，只允许更改分辨率。"""
    doc=ReviewDocument.model_validate(load_json(review))
    _emit(promote(doc,candidate,file,human_confirmed=human_confirmed,resolution=resolution),output)


@app.command('report')
@_guard
def report_command(review: Path, output: Path = typer.Option(...,'--output'),
                   reveal: bool = typer.Option(False,'--reveal')):
    """导出人读记录，默认隐藏文件名；没有自动总分或推荐获胜者。"""
    text=report(ReviewDocument.model_validate(load_json(review)),reveal=reveal)
    with output.open('x',encoding='utf-8') as stream:stream.write(text)
    _emit({'report':str(output),'source_revealed':reveal})
