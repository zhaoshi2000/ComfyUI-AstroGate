import copy
import sys

import pytest

from test_storyboard import modules as modules, project as project


@pytest.mark.parametrize('owner', ['林夏', '蓝衣'])
def test_character_and_look_sheet_request_has_one_identity_and_ordered_views(modules, project, owner):
    native = sys.modules[modules.node.__name__ + '.storyboard.native']
    asset = modules.domain.asset_by_id(project, owner)
    shot = native.asset_shot(project, asset)
    shot['asset_view'] = 'sheet'
    project['settings']['style'] = '水彩绘本'
    prompt = modules.graph.render_prompt(modules.graph.snapshot(project, shot), [])
    assert '一张完整的人物设定图册' in prompt
    assert '左侧为大幅头部至胸部特写' in prompt
    assert '右侧依次并排' in prompt and '正面、人物自身左侧90度侧面、背面' in prompt
    assert '全身视图，头顶到脚底完整不裁切' in prompt
    assert '同一张脸、同一体型和身高比例、同一套本次指定服装、发型和配饰' in prompt
    assert '干净的纯白或浅灰单色' in prompt and '其他人物、文字标签或水印' in prompt
    assert '画风：水彩绘本' in prompt
    assert shot['asset_kind'] == asset['kind']
    assert asset['parent_id'] == ('林夏' if owner == '蓝衣' else None)


@pytest.mark.parametrize('view', ['front', 'left', ''])
def test_legacy_character_views_do_not_become_sheets_without_opt_in(modules, project, view):
    native = sys.modules[modules.node.__name__ + '.storyboard.native']
    shot = native.asset_shot(project, modules.domain.asset_by_id(project, '林夏'))
    shot['asset_view'] = view
    prompt = modules.graph.render_prompt(modules.graph.snapshot(project, shot), [])
    assert '人物设定参考图：仅绘制一个指定人物' in prompt
    assert '头部至胸部特写' not in prompt and '三幅全身视图' not in prompt


@pytest.mark.parametrize('owner', ['林夏', '蓝衣'])
def test_new_asset_plan_defaults_to_single_sheet(modules, project, owner):
    planned = modules.graph.plan(modules.store, project, [], asset_id=owner, allow_missing=True)
    entry = planned['entries'][0]
    assert planned['requests'] == 1
    assert entry['snapshot']['shot']['asset_view'] == 'sheet'
    assert '一张完整的人物设定图册' in entry['prompt']


@pytest.mark.parametrize('kind, description', [('scene', '深夜客栈，人物在窗边交谈'), ('scene_state', '清晨山谷，薄雾中的山路与小屋')])
def test_scene_request_is_one_empty_continuous_environment_panorama(modules, project, kind, description):
    native = sys.modules[modules.node.__name__ + '.storyboard.native']
    asset = copy.deepcopy(modules.domain.asset_by_id(project, '客栈'))
    asset['kind'] = kind
    asset['versions'][0]['description'] = description
    project['settings']['style'] = '自然写实电影'
    shot = native.asset_shot(project, asset)
    prompt = modules.graph.render_prompt(modules.graph.snapshot(project, shot), [{'purpose': '同一场地空间基准'}])
    assert description in prompt
    if kind == 'scene':
        assert '一张16:9连续的场景全景母图，24mm等效广角、平视机位' in prompt
        assert '门窗、通道及主要陈设的相对位置' in prompt
        assert '室外对应展示地形、建筑、道路、植被' in prompt
        assert '主要光源方向、色温和投影关系' in prompt
        assert '禁止出现任何人物' in prompt and '参考图中即使有人也必须移除' in prompt
        assert '只用一个连续机位，不拼贴、不分屏、不做多角度拼图' in prompt
    else:
        assert '一张16:9连续的同场景补充全景' in prompt
        assert '与母图明显不同的空间透视' in prompt
        assert '不要复制母图的机位' in prompt
        assert '不擅自改变已经确定的布局' in prompt
    assert '文字、标签或水印' in prompt
    assert '画风：自然写实电影' in prompt
    assert '头部至胸部特写' not in prompt


def test_finished_scene_state_offers_new_default_without_changing_saved_image(modules, project):
    native = sys.modules[modules.node.__name__ + '.storyboard.native']
    state = copy.deepcopy(modules.domain.asset_by_id(project, '客栈'))
    state.update(id='清晨客栈', name='清晨客栈', kind='scene_state', parent_id='客栈', current_version_id='清晨客栈1')
    state['versions'][0].update(id='清晨客栈1', description='清晨光线照入客栈')
    project['assets'].append(state)
    project['shots'][0]['scene'] = modules.domain.binding(project, state['id'])
    previous = state['versions'][0]['description'] + '\n' + native.PREVIOUS_SCENE_MASTER_PROMPT + '\n' + native.SCENE_ONLY_PROMPT
    job = modules.store.add_job(project['id'], {'kind': 'image', 'status': 'succeeded', 'owner_id': state['id'],
        'owner_type': 'asset', 'asset_version_id': state['current_version_id'], 'asset_view': '',
        'snapshot': {'shot': {'image_prompt': previous}}, 'saved_result': {'filename': 'old.png', 'subfolder': '', 'type': 'input'}})
    recipe = native.native_canvas(modules.store, project, project['chapters'][0]['id'])
    card = next(item for item in recipe['nodes'] if item['id'] == state['id'])
    assert card['widgets']['job_id'] == job['id']
    assert card['widgets']['prompt'] == previous
    assert '与母图明显不同的空间透视' in card['default_prompt']


def test_shot_references_interpret_sheet_as_one_person_and_preserve_scene_layout(modules, project):
    shot = copy.deepcopy(project['shots'][0])
    shot.update(shot_size='手与钥匙的特写', camera_angle='俯拍', frozen_moment='钥匙触及地图的一瞬')
    prompt = modules.graph.render_prompt(modules.graph.snapshot(project, shot), [
        {'purpose': '林夏人物图册', 'asset_kind': 'character', 'asset_view': 'sheet'},
        {'purpose': '客栈全景母图', 'asset_kind': 'scene'},
    ])
    assert prompt.index('图1：林夏人物图册') < prompt.index('图2：客栈全景母图')
    assert '同一个角色的不同视图，不是多个人' in prompt
    assert '每个角色仅按本镜出场人数绘制，不复制成三人' in prompt
    assert '家具相对位置与主光源保持一致' in prompt
    assert '按本镜调整机位和景别' in prompt
    assert '只画本镜列出的可见人物' in prompt
    assert '本镜拍摄要求：手与钥匙的特写，俯拍，钥匙触及地图的一瞬' in prompt
    assert '不为了容纳人物参考图而退回全景' in prompt
    assert '不参考设定卡的全身构图或多视图排版' in prompt
    assert '一张16:9连续的场景全景母图' not in prompt
    assert '干净的纯白或浅灰单色' not in prompt
