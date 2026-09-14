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


@app.command('workspace-verify')
@_guard
def workspace_verify_command(archive: Path):
    """只读验证完整工作现场 ZIP；不解压、不恢复确认、不改影片工程。"""
    from .workspace import verify_workspace
    _emit(verify_workspace(archive))


@app.command('inspect-return')
@_guard
def inspect_return_command(request: Path, candidate: list[Path] = typer.Option(..., '--candidate'),
                           decode: bool = typer.Option(False, '--decode'),
                           pixel_width: Optional[int] = typer.Option(None, '--pixel-width'),
                           pixel_height: Optional[int] = typer.Option(None, '--pixel-height'),
                           output: Optional[Path] = typer.Option(None, '--output')):
    """只读核对返回视频的时长、画幅和最低短边；不打画质分、不选片。"""
    from .returns import inspect_files
    if (pixel_width is None) != (pixel_height is None):
        raise AuthoringError('provide both --pixel-width and --pixel-height')
    pixels = (pixel_width, pixel_height) if pixel_width is not None else None
    _emit(inspect_files(Request.model_validate(load_json(request)), candidate, decode=decode, expected_pixels=pixels), output)


@app.command('catalog-review')
@_guard
def catalog_review_command(before: Path, after: Path,
                           request: Optional[Path] = typer.Option(None, '--request'),
                           on: Optional[str] = typer.Option(None, '--on'),
                           output: Optional[Path] = typer.Option(None, '--output')):
    """逐字段比较能力修订与当前任务影响；日期不是远端核验或自动应用。"""
    from datetime import date
    from .catalog_review import review_catalog
    _emit(review_catalog(load_catalog(before), load_catalog(after),
          Request.model_validate(load_json(request)) if request else None,
          today=date.fromisoformat(on) if on else None), output)


@app.command('repair-create')
@_guard
def repair_create_command(request: Path, source: Path,
                          start_ms: int = typer.Option(..., '--start-ms'),
                          end_ms: int = typer.Option(..., '--end-ms'),
                          preserve: list[str] = typer.Option(..., '--preserve'),
                          change: list[str] = typer.Option(..., '--change'),
                          requested_by: str = typer.Option(..., '--requested-by'),
                          human_confirmed: bool = typer.Option(False, '--human-confirmed'),
                          before_ms: int = typer.Option(500, '--before-ms'),
                          after_ms: int = typer.Option(500, '--after-ms'),
                          audio_policy: str = typer.Option('preserve_original', '--audio-policy'),
                          output: Path = typer.Option(..., '--output')):
    """保存包含完整原片的局部返工ZIP；不剪切、不生成、不改原任务。"""
    from .repair import create_plan, write_repair_bundle
    from ..review.core import candidate_from_file
    if source.is_symlink():
        raise AuthoringError('select a regular source file, not a link')
    req = Request.model_validate(load_json(request))
    candidate = candidate_from_file(source, local_only=True)
    plan = create_plan(req, candidate, start_ms=start_ms, end_ms=end_ms,
                       preserve=preserve, change=change, requested_by=requested_by,
                       human_confirmed=human_confirmed, context_before_ms=before_ms,
                       context_after_ms=after_ms, audio_policy=audio_policy)
    _emit(write_repair_bundle(plan, source, output))


@app.command('repair-verify')
@_guard
def repair_verify_command(archive: Path):
    """只读核验返工范围、实际原片与文件清单，不解压或批准作品。"""
    from .repair import verify_repair_bundle
    _emit(verify_repair_bundle(archive))


@app.command('frontier-plan')
@_guard
def frontier_plan_command(request: Path, catalog: Optional[Path] = typer.Option(None, '--catalog'),
                          on: Optional[str] = typer.Option(None, '--on'),
                          output: Optional[Path] = typer.Option(None, '--output')):
    """仅列质量优先名单内的具体适配，不自动降级；保留证据身份与排除原因。"""
    from datetime import date
    from .quality import quality_plan
    _emit(quality_plan(Request.model_validate(load_json(request)), load_catalog(catalog),
                       today=date.fromisoformat(on) if on else None), output)


@app.command('director-verify')
@_guard
def director_verify_command(archive: Path):
    """只读核验运动底片、原帧和目标图；不生成、不上传、不自动批准。"""
    from .director import verify_director_bundle
    _emit(verify_director_bundle(archive))


@app.command('desk-verify')
@_guard
def desk_verify_command(archive: Path):
    """只读核验收工包及待处理材料，不恢复、批准或执行。"""
    from .desk import verify_desk
    _emit(verify_desk(archive))


@app.command('desk-extract-studio')
@_guard
def desk_extract_studio_command(archive: Path, output: Path = typer.Option(..., '--output')):
    """提取原字节 Studio/v1 给旧版本使用；拒绝覆盖已有文件。"""
    from .desk import extract_studio
    _emit(extract_studio(archive, output))


@app.command('studio-verify')
@_guard
def studio_verify_command(archive: Path):
    """只读核验三个工作区总备份，不解压、不恢复批准或执行生成。"""
    from .studio import verify_studio
    _emit(verify_studio(archive))


@app.command('studio-diff')
@_guard
def studio_diff_command(before: Path, after: Path,
                        output: Optional[Path] = typer.Option(None, '--output')):
    """只读比较两个完整备份的工作区、文字和媒体变化，不恢复现场。"""
    from .flexibility import difference, read_verified
    a, _ = read_verified(before)
    b, _ = read_verified(after)
    _emit(difference(a, b), output)


@app.command('studio-compose')
@_guard
def studio_compose_command(target: Path, donor: Path,
                           take: list[str] = typer.Option(..., '--take'),
                           output: Path = typer.Option(..., '--output')):
    """按shot/repair/director/catalog取用工作区，写全新总备份，不覆盖原包。"""
    from .flexibility import compose_archives
    _emit(compose_archives(target, donor, take, output))


@app.command('template-create')
@_guard
def template_create_command(archive: Path, name: str = typer.Option(..., '--name'),
                            area: Optional[list[str]] = typer.Option(None, '--area'),
                            output: Path = typer.Option(..., '--output')):
    """从有效总备份提取纯文字创作模板，不含素材、镜头身份或批准。"""
    from .flexibility import create_template, read_verified
    doc, _ = read_verified(archive)
    _emit(create_template(doc, name, areas=area), output)


@app.command('template-verify')
@_guard
def template_verify_command(template: Path):
    """只读核验个人模板的字段白名单，不应用到项目或执行其中的文字。"""
    from .flexibility import CreativeTemplate
    value = CreativeTemplate.model_validate(load_json(template))
    _emit({'ok': True, 'name': value.name, 'template_sha256': digest(value),
           'areas': list(value.fields), 'automatic_execution': False,
           'contains_approvals': False, 'contains_media': False})


@app.command('external-create')
@_guard
def external_create_command(archive: Path,
                            section: Optional[list[str]] = typer.Option(None, '--section'),
                            output: Path = typer.Option(..., '--output')):
    """导出可编辑文字 JSON；改 values，不改导出基线、批准或素材。"""
    from .exchange import create_edit
    from .flexibility import read_verified
    doc, _ = read_verified(archive)
    _emit(create_edit(doc, section), output)


@app.command('external-kit')
@_guard
def external_kit_command(archive: Path,
                         section: Optional[list[str]] = typer.Option(None, '--section'),
                         without_media: bool = typer.Option(False, '--without-media'),
                         output: Path = typer.Option(..., '--output')):
    """按区域导出 TXT、可编辑 JSON 与原素材，不转码，不覆盖。"""
    from .exchange import export_kit
    _emit(export_kit(archive, output, section, include_media=not without_media))


@app.command('external-texts')
@_guard
def external_texts_command(edit: Path, text: list[Path] = typer.Option(..., '--text'),
                           output: Path = typer.Option(..., '--output')):
    """把外部 UTF-8 text-材料ID-xxx.txt 带回对应 EDIT.json，尚不应用。"""
    from .exchange import overlay_texts, read_edit
    files = {}
    total = 0
    for path in text:
        if path.is_symlink() or not path.is_file() or path.name in files:
            raise AuthoringError('text files must be regular with unique basenames')
        with path.open('rb') as stream:
            data = stream.read(120004)
        if len(data) > 120003:
            raise AuthoringError('text file exceeds UTF-8 bound')
        total += len(data)
        if total > 2 * 1024 * 1024:
            raise AuthoringError('selected text exceeds 2 MiB')
        files[path.name] = data
    _emit(overlay_texts(read_edit(edit), files), output)


@app.command('external-preview')
@_guard
def external_preview_command(archive: Path, edit: Path,
                             output: Optional[Path] = typer.Option(None, '--output')):
    """只读三方比较；输出预览哈希，应用时须传回以阻断过时预览。"""
    from .exchange import preview_edit, read_edit
    from .flexibility import read_verified
    doc, _ = read_verified(archive)
    preview = preview_edit(doc, read_edit(edit))
    _emit({'preview': preview, 'preview_sha256': digest(preview)}, output)


@app.command('external-apply')
@_guard
def external_apply_command(archive: Path, edit: Path,
                           take: list[str] = typer.Option(..., '--take'),
                           preview_sha256: str = typer.Option(..., '--preview-sha256'),
                           confirm: bool = typer.Option(False, '--confirm'),
                           output: Path = typer.Option(..., '--output')):
    """逐字段确认后新建总备份；原包、媒体和历史决定保持不变。"""
    from .exchange import apply_archive, read_edit
    if not confirm:
        raise AuthoringError('preview first, then explicitly pass --confirm with selected fields')
    _emit(apply_archive(archive, read_edit(edit), take, output, expected_preview=preview_sha256))


@app.command('retouch-verify')
@_guard
def retouch_verify_command(archive: Path):
    """只读核验静帧精修交接包；不是生成服务、完整备份或画质认证。"""
    from .retouch import verify_kit
    _emit(verify_kit(archive))


@app.command('story-check')
@_guard
def story_check_command(story: Path):
    """只读核验故事工作本及简报依赖版本；不是剧情或画质自动验收。"""
    from .story import read_story, report
    _emit(report(read_story(story)))


@app.command('story-brief')
@_guard
def story_brief_command(story: Path, scene: str, output: Path = typer.Option(..., '--output')):
    """按已保存的范围与真实场序导出当前场景上下文，不覆盖已有文件。"""
    from .story import compile_brief, read_story
    _emit(compile_brief(read_story(story), scene), output)


@app.command('story-from-series')
@_guard
def story_from_series_command(series: Path, output: Path = typer.Option(..., '--output')):
    """只读接入原剧集共享 Bible；默认作者资料，不猜测角色知情或剧情场景。"""
    from .story import from_series
    _emit(from_series(series), output)


@app.command('story-kit-verify')
@_guard
def story_kit_verify_command(archive: Path):
    """只读核验简报与原参考字节，不代表当前依赖、语义、画质或模型身份认证。"""
    from .story import verify_brief_kit
    _emit(verify_brief_kit(archive))


def _ide_call(function, *args, **kwargs):
    """IDE tools use structured errors; existing command error contracts stay intact."""
    try:
        _emit(function(*args, **kwargs))
    except (AuthoringError, ValidationError, ValueError, OSError) as exc:
        _emit({'ok': False, 'error': str(exc), 'code': 'ide_handoff_invalid',
               'automatic_execution': False})
        raise typer.Exit(2) from exc


@app.command('ide-open')
def ide_open_command(archive: Path, output: Path = typer.Option(..., '--output')):
    """将已保存的收工包转成新的 IDE 可编辑工作目录。原包不改，不执行素材。"""
    from .ide import open_workspace
    _ide_call(open_workspace, archive, output)


@app.command('ide-preview')
def ide_preview_command(workspace: Path, html: Path | None = typer.Option(None, '--html')):
    """只读检查 AI 工作副本，列出改变和过期简报，生成绑定本次内容的预览哈希。"""
    from .ide import preview_workspace
    _ide_call(preview_workspace, workspace, html_output=html)


@app.command('ide-return')
def ide_return_command(workspace: Path, output: Path = typer.Option(..., '--output'),
                       expected_preview: str = typer.Option(..., '--expected-preview')):
    """明确以刚才预览创建新收工候选包；不自动恢复，不覆盖原包或后来浏览器稿。"""
    from .ide import return_workspace
    _ide_call(return_workspace, workspace, output, expected_preview=expected_preview)


@app.command('ide-story-return')
def ide_story_return_command(workspace: Path, output: Path = typer.Option(..., '--output'),
                             expected_preview: str = typer.Option(..., '--expected-preview')):
    """导出带原稿的故事修改JSON，逐项接回，不含镜头EDIT或媒体。"""
    from .ide import export_story_return
    _ide_call(export_story_return, workspace, output, expected_preview=expected_preview)


@app.command('story-return-preview')
def story_return_preview_command(current: Path, returned: Path):
    """只读三方比较当前故事、导出原稿和返回新稿。"""
    from .story_return import preview_files
    _ide_call(preview_files, current, returned)


@app.command('story-return-apply')
def story_return_apply_command(current: Path, returned: Path,
                               output: Path = typer.Option(..., '--output'),
                               take: list[str] = typer.Option(..., '--take'),
                               expected_preview: str = typer.Option(..., '--expected-preview')):
    """仅按有效预览的明确选择创建新故事；冲突拒绝，原件不覆盖。"""
    from .story_return import apply_files
    _ide_call(apply_files, current, returned, output, take, expected_preview=expected_preview)
