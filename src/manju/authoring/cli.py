"""CLI for offline authoring, intentionally not an execution provider."""
from __future__ import annotations
from pathlib import Path
from typing import Optional
import json
import typer
from pydantic import ValidationError
from .core import (AuthoringError, Request, Approval, load_catalog, load_json,
                   write_new_json, plan, approve, write_bundle, verify_bundle, digest)

app = typer.Typer(no_args_is_help=True, help='离线型号校验、人工确认与素材交接。不调用付费服务。')


def _guard(fn):
    from functools import wraps
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AuthoringError, ValidationError, ValueError, OSError) as exc:
            typer.echo(f'模型交接未完成：{exc}', err=True)
            raise typer.Exit(2) from exc
    return call


def _emit(value, output: Path | None = None):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if output is not None:
        write_new_json(output, value)
    else:
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command('catalog')
@_guard
def catalog_command(catalog: Optional[Path] = typer.Option(None, '--catalog'),
                    output: Optional[Path] = typer.Option(None, '--output')):
    """查看/验证能力档；自定义档只读取，不下载、不执行。"""
    _emit(load_catalog(catalog), output)


@app.command('example')
@_guard
def example_command(output: Path = typer.Option(..., '--output')):
    """创建新任务示例，不修改任何影片工程。"""
    _emit(Request(shot_id='示例镜头', task='create', prompt='一个连续镜头：雨停后，霓虹倒影缓慢消散。',
                  duration_s=8, resolution='720p', aspect_ratio='16:9',
                  preserve=['单个连续镜头'], change=[]), output)


@app.command('plan')
@_guard
def plan_command(request: Path, catalog: Optional[Path] = typer.Option(None, '--catalog'),
                 output: Optional[Path] = typer.Option(None, '--output')):
    """列出适配候选及拒绝理由；不替用户选型号。"""
    _emit(plan(Request.model_validate(load_json(request)), load_catalog(catalog)), output)


@app.command('approve')
@_guard
def approve_command(request: Path, profile: str = typer.Option(..., '--profile'),
                    mode: str = typer.Option(..., '--mode'),
                    reviewer: str = typer.Option(..., '--reviewer'),
                    human_confirmed: bool = typer.Option(False, '--human-confirmed'),
                    acknowledge_warnings: bool = typer.Option(False, '--acknowledge-warnings'),
                    catalog: Optional[Path] = typer.Option(None, '--catalog'),
                    output: Path = typer.Option(..., '--output')):
    """记录人工声明，不签名、不授权执行或付费。先查看 plan 的告警。"""
    req, cat = Request.model_validate(load_json(request)), load_catalog(catalog)
    _emit(approve(req, cat, profile, mode, reviewer=reviewer,
                  human_confirmed=human_confirmed, acknowledge_warnings=acknowledge_warnings), output)


@app.command('bundle')
@_guard
def bundle_command(request: Path, approval: Path,
                   asset_root: Path = typer.Option(Path('.'), '--asset-root'),
                   catalog: Optional[Path] = typer.Option(None, '--catalog'),
                   draft_review: Optional[Path] = typer.Option(None, '--draft-review'),
                   draft_file: Optional[Path] = typer.Option(None, '--draft-file'),
                   output: Path = typer.Option(..., '--output')):
    """校验实际素材字节并创建一个新目录；绝不覆盖既有交接包。"""
    path = write_bundle(Request.model_validate(load_json(request)), load_catalog(catalog),
                        Approval.model_validate(load_json(approval)), asset_root, output,
                        draft_review=load_json(draft_review) if draft_review else None, draft_file=draft_file)
    _emit({'bundle': str(path), **verify_bundle(path)})


@app.command('verify')
@_guard
def verify_command(bundle: Path, require_promotion: bool = typer.Option(False, '--require-promotion')):
    """核验包、批准、素材、说明的一致性，不代表供应商可接受。"""
    _emit(verify_bundle(bundle, require_promotion=require_promotion))


@app.command('diff')
@_guard
def diff_command(before: Path, after: Path):
    """比较两个能力档，指出新增、删除、修改，不改写项目。"""
    a, b = load_catalog(before), load_catalog(after)
    old, new = {p.id: digest(p) for p in a.profiles}, {p.id: digest(p) for p in b.profiles}
    _emit({'before_sha256': digest(a), 'after_sha256': digest(b),
           'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
           'changed': sorted(k for k in old.keys() & new.keys() if old[k] != new[k])})


@app.command('from-shot')
@_guard
def from_shot_command(project: Path, shot_id: str,
                      output: Path = typer.Option(..., '--output')):
    """只读导出镜头和参考计划，供离线工作台导入；不改原工程。"""
    from .project_bridge import from_shot
    _emit(from_shot(project, shot_id), output)


@app.command('workbench')
@_guard
def workbench_command(output: Path = typer.Option(..., '--output')):
    """导出单文件离线工作台；双击可用，不需要运行本地服务器。"""
    from importlib.resources import files
    data = files('manju.authoring').joinpath('data/workbench.html').read_bytes()
    with output.open('xb') as stream:
        stream.write(data)
    _emit({'workbench': str(output), 'network_required': False,
           'note': '离线规划与文件交接，不生成商业模型视频。'})
