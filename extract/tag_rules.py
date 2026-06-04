#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多维度自动标签引擎
===================
为每道题从 5 个维度自动打标签：
  1. 知识点 (knowledge)   — 学科核心概念
  2. 能力维度 (ability)   — 考查的学生能力
  3. 题型特征 (feature)   — 题目呈现形式
  4. 解题方法 (method)    — 常用解题策略
  5. 考试定位 (position)  — 试卷中的功能定位

返回结构：
{
  "knowledge": ["圆", "几何"],
  "ability": ["逻辑推理", "空间想象"],
  "feature": ["含图表", "多步骤"],
  "method": ["数形结合", "分类讨论"],
  "position": "压轴题"
}
"""

import re
from typing import Dict, List, Tuple

# =============================================================================
# 维度 1: 知识点标签 (Knowledge)
# =============================================================================

MATH_KNOWLEDGE_RULES: List[Tuple[re.Pattern, List[str]]] = [
    # 数与式
    (re.compile(r"科学记数法|10\^|×10[⁰¹²³⁴⁵⁶⁷⁸⁹]|\d\.\d+[××]10\^"), ["科学记数法", "数与式"]),
    (re.compile(r"实数|有理数|无理数|整数|分数|小数|正负数|相反数|绝对值|倒数"), ["实数", "数与式"]),
    (re.compile(r"整式|单项式|多项式|同类项|合并同类项|去括号|添括号"), ["整式", "数与式"]),
    (re.compile(r"因式分解|提公因式|公式法|十字相乘|平方差|完全平方"), ["因式分解", "数与式"]),
    (re.compile(r"分式|分母.*不为零|有意义条件|分式方程|约分|通分"), ["分式", "数与式"]),
    (re.compile(r"二次根式|根号|√|算术平方根|最简二次根式|分母有理化"), ["二次根式", "数与式"]),
    
    # 方程不等式
    (re.compile(r"一元一次方程|解方程|方程的解|移项|去分母"), ["一元一次方程", "方程不等式"]),
    (re.compile(r"二元一次方程组|代入消元|加减消元|方程组"), ["二元一次方程组", "方程不等式"]),
    (re.compile(r"一元二次方程|判别式|Δ|根.*关系|韦达|求根公式|配方法解方程"), ["一元二次方程", "方程不等式"]),
    (re.compile(r"分式方程|增根|去分母.*方程"), ["分式方程", "方程不等式"]),
    (re.compile(r"不等式.*组|解集.*数轴|一元一次不等式|不等号"), ["不等式", "方程不等式"]),
    
    # 函数
    (re.compile(r"一次函数|正比例函数|y\s*=\s*kx\s*\+\s*b|k\s*>\s*0|k\s*<\s*0|斜率"), ["一次函数", "函数"]),
    (re.compile(r"反比例函数|双曲线|y\s*=\s*k\s*/\s*x|k\s*>\s*0|k\s*<\s*0|渐近线"), ["反比例函数", "函数"]),
    (re.compile(r"二次函数|抛物线|y\s*=\s*ax[²2]|顶点.*坐标|对称轴|开口.*方向|最值"), ["二次函数", "函数"]),
    (re.compile(r"函数图像|图象|描点|列表|坐标系|x轴|y轴|原点"), ["函数图像", "函数"]),
    
    # 几何 — 三角形
    (re.compile(r"全等.*三角形|SSS|SAS|ASA|AAS|HL|全等判定|全等证明"), ["全等三角形", "三角形", "几何"]),
    (re.compile(r"相似.*三角形|相似比|位似|相似判定|AA|SAS相似|SSS相似"), ["相似三角形", "三角形", "几何"]),
    (re.compile(r"等腰.*三角形|等边.*三角形|直角.*三角形|勾股定理|勾股数"), ["特殊三角形", "三角形", "几何"]),
    (re.compile(r"三角形.*内角|外角|中线|高线|角平分线|中位线"), ["三角形性质", "三角形", "几何"]),
    (re.compile(r"解直角三角形|三角函数|sin|cos|tan|仰角|俯角|坡度"), ["解直角三角形", "三角形", "几何"]),
    
    # 几何 — 四边形
    (re.compile(r"平行四边形|矩形|菱形|正方形|梯形|对角线|中点四边形"), ["四边形", "几何"]),
    
    # 几何 — 圆
    (re.compile(r"圆[的O与].*切线|切线.*圆|切线长|切点"), ["圆的切线", "圆", "几何"]),
    (re.compile(r"圆周角|圆心角|弧长|扇形|垂径定理|弦.*直径"), ["圆的性质", "圆", "几何"]),
    (re.compile(r"圆内接|外接圆|内切圆|外切圆|四点共圆"), ["圆与多边形", "圆", "几何"]),
    
    # 几何 — 其他
    (re.compile(r"尺规.*作图|作图.*保留.*痕迹|作图题"), ["尺规作图", "几何"]),
    (re.compile(r"多边形.*内角|外角和|正多边形|对角线"), ["多边形", "几何"]),
    (re.compile(r"平移|旋转|轴对称|中心对称|翻折|折叠"), ["图形变换", "几何"]),
    (re.compile(r"三视图|展开图|立体图形|正方体.*展开|圆柱.*圆锥"), ["立体几何", "几何"]),
    
    # 统计概率
    (re.compile(r"统计.*方差|中位数.*众数|频数分布|数据分析|平均数|加权平均"), ["统计", "数据分析"]),
    (re.compile(r"概率|随机|树状图|列举法|频率|等可能|样本空间"), ["概率", "统计"]),
    (re.compile(r"抽样|总体|个体|样本|样本容量|普查|抽查"), ["抽样调查", "统计"]),
    (re.compile(r"条形图|扇形图|折线图|直方图|频数分布表"), ["统计图表", "统计"]),
    
    # 综合
    (re.compile(r"存在性|最值|最大值|最小值|取值范围|恒成立|参数范围"), ["存在性最值", "综合"]),
    (re.compile(r"新定义|阅读.*理解|材料.*题|概念.*学习|定义.*运算"), ["新定义", "综合"]),
    (re.compile(r"动点|动圆|轨迹|路径长|最值.*路径"), ["动态几何", "综合"]),
]

PHYSICS_KNOWLEDGE_RULES: List[Tuple[re.Pattern, List[str]]] = [
    # 力学
    (re.compile(r"浮力.*探究|阿基米德|排水.*体积|F[_\s]?浮|G[_\s]?排|浮力.*计算"), ["浮力", "力学"]),
    (re.compile(r"摩擦力.*因素|滑动摩擦|静摩擦|增大.*减小.*摩擦|摩擦.*测量"), ["摩擦力", "力学"]),
    (re.compile(r"压强.*液体|大气压|帕斯卡|连通器|液压|压强.*计算"), ["压强", "力学"]),
    (re.compile(r"杠杆.*平衡|力臂|省力杠杆|费力杠杆|等臂杠杆|杠杆.*条件"), ["杠杆", "力学"]),
    (re.compile(r"滑轮|定滑轮|动滑轮|滑轮组|机械效率|有用功|额外功|总功"), ["滑轮组", "力学"]),
    (re.compile(r"密度.*测量|测密度|ρ\s*=\s*m/V|质量.*体积|天平.*量筒"), ["密度测量", "力学"]),
    (re.compile(r"重力|弹力|支持力|压力|拉力|推力|二力平衡|合力"), ["力的概念", "力学"]),
    (re.compile(r"牛顿.*定律|惯性|运动.*状态|加速|减速|匀速"), ["牛顿定律", "力学"]),
    (re.compile(r"功|功率|W\s*=\s*Fs|P\s*=\s*W/t|焦耳|瓦特"), ["功和功率", "力学"]),
    
    # 电学
    (re.compile(r"欧姆定律|I\s*=\s*U/R|电阻.*电流.*电压|U\s*=\s*IR"), ["欧姆定律", "电学"]),
    (re.compile(r"电功率|P\s*=\s*UI|额定功率|实际功率|W\s*=\s*Pt|电能表"), ["电功率", "电学"]),
    (re.compile(r"伏安法.*电阻|电流表.*电压表.*测电阻|测.*小灯泡.*功率|电表.*读数"), ["伏安法测电阻", "电学"]),
    (re.compile(r"电路.*串联|并联.*电路|电流.*电压.*关系|串.*联.*分压|并.*联.*分流"), ["电路分析", "电学"]),
    (re.compile(r"电阻|变阻器|滑动变阻器|电阻箱|电阻.*因素|电阻率"), ["电阻", "电学"]),
    (re.compile(r"电功|电能|电热|焦耳定律|Q\s*=\s*I[²2]Rt|电流.*热效应"), ["电热", "电学"]),
    (re.compile(r"家庭电路|保险丝|开关|插座|火线|零线|地线|触电"), ["家庭电路", "电学"]),
    (re.compile(r"电磁|磁场|磁感线|电流.*磁效应|电动机|发电机|电磁感应"), ["电磁", "电学"]),
    
    # 光学
    (re.compile(r"凸透镜.*成像|焦距.*像距|u\s*v|物距.*像距|透镜.*规律"), ["凸透镜成像", "光学"]),
    (re.compile(r"光的反射|平面镜|入射角|反射角|反射定律|镜面反射|漫反射"), ["光的反射", "光学"]),
    (re.compile(r"光的折射|透镜|折射角|折射定律|光的色散|光谱"), ["光的折射", "光学"]),
    
    # 热学
    (re.compile(r"物态变化|熔化.*凝固|汽化.*液化|升华.*凝华|熔点|沸点"), ["物态变化", "热学"]),
    (re.compile(r"比热容|热量.*计算|Q\s*=\s*cmΔt|吸热.*放热|热传递"), ["比热容", "热学"]),
    (re.compile(r"温度|温度计|摄氏|华氏|热胀冷缩"), ["温度", "热学"]),
    (re.compile(r"内能|热量|热机|效率|能量.*转化|守恒"), ["内能与热机", "热学"]),
    
    # 声学 / 其他
    (re.compile(r"声音|音调|响度|音色|声波|振动|频率|振幅|噪声"), ["声学", "物理"]),
    (re.compile(r"速度|匀速|变速|平均速度|路程.*时间|s\s*=\s*vt"), ["运动学", "物理"]),
    
    # 实验探究
    (re.compile(r"控制变量|自变量|因变量|控制.*不变|改变.*研究|多次测量"), ["控制变量", "实验方法"]),
    (re.compile(r"实验.*设计|实验.*步骤|实验.*器材|实验.*结论|实验.*误差"), ["实验设计", "实验方法"]),
    (re.compile(r"测量型实验|读数|估读|有效数字|误差分析"), ["测量型实验", "实验方法"]),
    (re.compile(r"探究.*关系|探究.*因素|探究.*规律|猜想.*验证"), ["探究实验", "实验方法"]),
    
    # 科普阅读
    (re.compile(r"科普.*阅读|阅读.*材料|根据.*短文|材料.*分析|信息.*提取"), ["科普阅读", "阅读"]),
]


# =============================================================================
# 维度 2: 能力维度标签 (Ability)
# =============================================================================

ABILITY_RULES: List[Tuple[re.Pattern, List[str]]] = [
    (re.compile(r"计算|运算|求解|求值|化简|结果.*是|等于.*多少|数值"), ["计算能力"]),
    (re.compile(r"证明|求证|说明.*理由|为什么|因为.*所以|推导|推出"), ["逻辑推理"]),
    (re.compile(r"如图|图形|图像|示意图|几何.*图|坐标系|画图"), ["空间想象"]),
    (re.compile(r"阅读.*材料|根据.*短文|材料.*题|新定义|概念.*学习|理解.*概念"), ["阅读理解"]),
    (re.compile(r"实验.*设计|实验.*步骤|选择.*器材|设计.*方案|如何.*操作"), ["实验设计"]),
    (re.compile(r"表格|数据.*分析|统计图|折线图|柱状图|处理.*数据|分析.*数据"), ["数据分析"]),
    (re.compile(r"实际|生活|应用|情境|问题|建模|模型|抽象"), ["建模能力"]),
    (re.compile(r"猜想|归纳|类比|推广|一般化|特殊化|抽象.*概括"), ["归纳抽象"]),
    (re.compile(r"分类|分情况|当.*时|若.*则|不同.*情况|各种.*情形"), ["分类讨论"]),
]


# =============================================================================
# 维度 3: 题型特征标签 (Feature)
# =============================================================================

FEATURE_RULES: List[Tuple[re.Pattern, List[str]]] = [
    (re.compile(r"如图|图形|图像|示意图|照片|表格|图表|折线图|柱状图|扇形图"), ["含图表"]),
    (re.compile(r"第一步|第二步|首先.*然后|先.*再.*最后|过程|步骤|分步"), ["多步骤"]),
    (re.compile(r"实际|生活|应用|情境|问题|工程|建筑|运动|购物|旅行"), ["实际情境"]),
    (re.compile(r"至少|至多|最大|最小|最优|最值|范围|区间|取值"), ["最值问题"]),
    (re.compile(r"是否存在|能否|有没有|可不可以|是否.*成立|判断.*存在"), ["存在性"]),
    (re.compile(r"证明|求证|说明.*理由|严格证明|证明.*成立"), ["证明题"]),
    (re.compile(r"开放|多种|不同.*方法|不唯一|至少.*种|任意|所有"), ["开放性"]),
    (re.compile(r"单选|选择.*正确|下列.*正确|错误.*的是"), ["单选题"]),
    (re.compile(r"填空|填写|补全|横线|空格|处应填"), ["填空题"]),
    (re.compile(r"解答|计算题|求解|求.*值|过程|写出.*步骤"), ["解答题"]),
    (re.compile(r"综合|多个.*知识点|结合|联系|融合|跨.*章节"), ["综合题"]),
    (re.compile(r"新定义|定义.*运算|规定|符号.*表示|新概念"), ["新定义题"]),
    (re.compile(r"阅读.*理解|材料.*题|根据.*材料|信息.*题"), ["材料阅读题"]),
    (re.compile(r"动手|操作|折叠|剪拼|旋转.*实物|实验.*操作"), ["操作题"]),
]


# =============================================================================
# 维度 4: 解题方法标签 (Method)
# =============================================================================

METHOD_RULES: List[Tuple[re.Pattern, List[str]]] = [
    (re.compile(r"数形结合|画图.*分析|图像.*帮助|坐标.*几何|代数.*几何"), ["数形结合"]),
    (re.compile(r"分类讨论|分情况|当.*时|若.*则|各种.*情况|不同.*情形|分.*类"), ["分类讨论"]),
    (re.compile(r"构造|辅助线|辅助圆|辅助函数|补形|割补|作.*辅助"), ["构造法"]),
    (re.compile(r"反证|假设.*不成立|假设.*反面|矛盾|不可能"), ["反证法"]),
    (re.compile(r"换元|设.*为t|变量.*替换|代换|令.*等于"), ["换元法"]),
    (re.compile(r"配方|完全平方|配成.*平方|配方法"), ["配方法"]),
    (re.compile(r"待定系数|设.*为k|设.*为a|系数.*确定|比较.*系数"), ["待定系数法"]),
    (re.compile(r"整体|整体代入|整体.*思想|看作.*整体|换.*整体"), ["整体代入"]),
    (re.compile(r"方程.*思想|列方程|设未知数|用方程|建立.*方程"), ["方程思想"]),
    (re.compile(r"函数.*思想|构造函数|建立.*函数|用函数"), ["函数思想"]),
    (re.compile(r"转化|化归|转化.*为|变为|等价.*于|划归"), ["转化化归"]),
    (re.compile(r"特殊值|取.*值|令.*等于|举例|代入.*具体"), ["特殊值法"]),
    (re.compile(r"排除|排除法|逐一.*排除|不可能.*是|显然.*不对"), ["排除法"]),
    (re.compile(r"归纳|递推|找.*规律|猜想|观察.*规律|总结.*规律"), ["归纳法"]),
    (re.compile(r"对称|利用.*对称|对称性|轴对称|中心对称.*性质"), ["对称法"]),
    (re.compile(r"面积法|等积|面积.*相等|等面积"), ["面积法"]),
]


# =============================================================================
# 维度 5: 考试定位标签 (Position)
# =============================================================================

# 考试定位由题号推断，非正则匹配
def infer_position_tag(question_number: int, difficulty: int, q_type: str) -> str:
    """根据题号、难度、类型推断考试定位"""
    if question_number <= 8:
        return "基础题"
    elif question_number <= 15:
        return "中档题"
    elif question_number <= 22:
        if q_type == "proof" or difficulty >= 3:
            return "中档题"
        return "中档题"
    elif question_number <= 26:
        return "综合题"
    elif question_number >= 27:
        return "压轴题"
    return "中档题"


# =============================================================================
# 主标签引擎
# =============================================================================

def auto_tag(content: str, subject: str, question_number: int = 0, 
             difficulty: int = 2, q_type: str = "comprehensive") -> Dict[str, List[str]]:
    """
    多维度自动标签引擎
    
    Args:
        content: 题目文本内容
        subject: 'math' 或 'physics'
        question_number: 题号（用于推断考试定位）
        difficulty: 难度（1-5）
        q_type: 题目类型
    
    Returns:
        {
            "knowledge": ["圆", "几何"],
            "ability": ["逻辑推理", "空间想象"],
            "feature": ["含图表", "多步骤"],
            "method": ["数形结合"],
            "position": "压轴题"
        }
    """
    if not content:
        return {
            "knowledge": ["未分类"],
            "ability": [],
            "feature": [],
            "method": [],
            "position": "中档题"
        }
    
    # 1. 知识点标签
    knowledge_tags = set()
    rules = MATH_KNOWLEDGE_RULES if subject == "math" else PHYSICS_KNOWLEDGE_RULES
    for pattern, tags in rules:
        if pattern.search(content):
            knowledge_tags.update(tags)
    
    if not knowledge_tags:
        knowledge_tags.add("未分类")
    
    # 2. 能力维度标签
    ability_tags = set()
    for pattern, tags in ABILITY_RULES:
        if pattern.search(content):
            ability_tags.update(tags)
    
    # 3. 题型特征标签
    feature_tags = set()
    for pattern, tags in FEATURE_RULES:
        if pattern.search(content):
            feature_tags.update(tags)
    
    # 4. 解题方法标签
    method_tags = set()
    for pattern, tags in METHOD_RULES:
        if pattern.search(content):
            method_tags.update(tags)
    
    # 5. 考试定位标签
    position_tag = infer_position_tag(question_number, difficulty, q_type)
    
    return {
        "knowledge": sorted(list(knowledge_tags)),
        "ability": sorted(list(ability_tags)),
        "feature": sorted(list(feature_tags)),
        "method": sorted(list(method_tags)),
        "position": position_tag
    }


def merge_tags(tag_result: Dict[str, List[str]]) -> List[str]:
    """将多维度标签合并为统一列表（用于存储到 tags 字段）"""
    all_tags = []
    all_tags.extend(tag_result.get("knowledge", []))
    all_tags.extend(tag_result.get("ability", []))
    all_tags.extend(tag_result.get("feature", []))
    all_tags.extend(tag_result.get("method", []))
    position = tag_result.get("position", "")
    if position:
        all_tags.append(position)
    return sorted(list(set(all_tags)))


# =============================================================================
# 便捷函数
# =============================================================================

def get_tag_summary(content: str, subject: str, question_number: int = 0,
                    difficulty: int = 2, q_type: str = "comprehensive") -> Dict:
    """获取完整标签摘要（含统计信息）"""
    tags = auto_tag(content, subject, question_number, difficulty, q_type)
    merged = merge_tags(tags)
    
    return {
        "dimensions": tags,
        "merged": merged,
        "count": len(merged),
        "has_knowledge": len(tags["knowledge"]) > 0 and tags["knowledge"][0] != "未分类",
        "has_ability": len(tags["ability"]) > 0,
        "has_feature": len(tags["feature"]) > 0,
        "has_method": len(tags["method"]) > 0,
    }
