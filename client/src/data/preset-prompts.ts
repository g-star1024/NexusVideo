/*
 * NexusVideo 预设 Prompt 模板库
 * ============================================================
 * 15 个经过 ComfyUI 测试的英文提示词模板
 * 6 个分类：landscape / lifestyle / product / advertising / education / abstract
 *
 * 使用方式：在 Text2VideoView.vue 中通过 import 引入，
 *           点击推荐场景卡片后自动填充到 prompt 文本框
 */

export interface PresetPrompt {
  id: string;
  category: 'landscape' | 'lifestyle' | 'product' | 'advertising' | 'education' | 'abstract';
  title: string;
  icon: string;
  prompt: string;
  tags: string[];
}

export const PRESET_PROMPTS: PresetPrompt[] = [
  // === 风景自然类 ===
  {
    id: 'scene-01',
    category: 'landscape',
    title: '壮丽日出云海',
    icon: '🌅',
    tags: ['风景', '日出', '云海'],
    prompt:
      'A breathtaking cinematic aerial shot of sunrise over a vast sea of clouds, golden light breaking through, mountains in the distance, soft volumetric fog, epic wide angle, photorealistic, 8K, smooth slow zoom-in',
  },
  {
    id: 'scene-02',
    category: 'landscape',
    title: '森林溪流晨光',
    icon: '🌲',
    tags: ['风景', '森林', '溪流'],
    prompt:
      'A serene forest stream at dawn, sunlight filtering through tall pine trees, gentle flowing water with sparkling reflections, mossy rocks, dewdrops on ferns, peaceful atmosphere, nature documentary style, shallow depth of field, 4K',
  },
  {
    id: 'scene-03',
    category: 'landscape',
    title: '暴风雨海面',
    icon: '🌊',
    tags: ['风景', '海洋', '暴风雨'],
    prompt:
      'Dramatic stormy ocean with massive waves crashing against rocky cliffs, dark clouds rolling across the sky, lightning flashes, seawater spray, cinematic wide shot, high contrast, intense atmosphere, IMAX quality',
  },
  // === 人物生活类 ===
  {
    id: 'scene-04',
    category: 'lifestyle',
    title: '都市街头漫步',
    icon: '🌃',
    tags: ['人物', '都市', '街头'],
    prompt:
      'A young woman walking through a rainy urban street at night, neon signs reflecting on wet pavement, holding an umbrella, cinematic tracking shot from behind, shallow depth of field, bokeh lights, film grain, moody atmosphere, 8K',
  },
  {
    id: 'scene-05',
    category: 'lifestyle',
    title: '办公室专注工作',
    icon: '💼',
    tags: ['人物', '办公', '专业'],
    prompt:
      'A professional in a modern minimalist office, focused on a laptop screen, natural window light, subtle camera pan, clean desk setup, warm color grade, realistic skin texture, corporate lifestyle photography style, 4K',
  },
  {
    id: 'scene-06',
    category: 'lifestyle',
    title: '咖啡馆悠闲时光',
    icon: '☕',
    tags: ['人物', '咖啡', '休闲'],
    prompt:
      'A person sitting in a cozy cafe by a large window, steam rising from a coffee cup, soft afternoon sunlight, books and plants nearby, gentle camera movement, warm tones, lifestyle vlog aesthetic, shallow focus, 4K',
  },
  // === 产品展示类 ===
  {
    id: 'scene-07',
    category: 'product',
    title: '产品360°悬浮',
    icon: '📦',
    tags: ['产品', '展示', '旋转'],
    prompt:
      'A sleek product floating in mid-air against a clean white background, slow 360-degree rotation, soft studio lighting, subtle reflections, premium product photography, ultra clean composition, 8K commercial quality',
  },
  {
    id: 'scene-08',
    category: 'product',
    title: '美食特写',
    icon: '🍽️',
    tags: ['美食', '特写', '烹饪'],
    prompt:
      'Extreme close-up of a gourmet dish, steam rising, golden caramelized crust, cherry tomatoes and fresh herbs, overhead camera angle with slow descent, food photography magazine style, warm tones, shallow depth of field, 8K',
  },
  {
    id: 'scene-09',
    category: 'product',
    title: '科技产品开箱',
    icon: '📱',
    tags: ['科技', '开箱', 'ASMR'],
    prompt:
      'Top-down view of a premium tech product being unboxed, hands carefully removing protective packaging, clean white surface, soft overhead lighting, ASMR-style macro shots, satisfying reveal, high detail, 4K commercial',
  },
  // === 广告营销类 ===
  {
    id: 'scene-10',
    category: 'advertising',
    title: '品牌开场动画',
    icon: '✨',
    tags: ['广告', '品牌', '动画'],
    prompt:
      'Dynamic abstract geometric shapes flowing and morphing into a brand logo, particle effects, glowing edges, dark background with neon accents, fast-paced energetic motion, cinematic 3D render, premium brand intro style, 4K',
  },
  {
    id: 'scene-11',
    category: 'advertising',
    title: '运动品牌广告',
    icon: '🏃',
    tags: ['广告', '运动', '慢动作'],
    prompt:
      'A runner sprinting through a city at golden hour, motion blur on background, slow-motion capture of feet hitting the pavement, sweat droplets flying, empowering atmosphere, dynamic camera tracking, sports commercial quality, 4K',
  },
  {
    id: 'scene-12',
    category: 'advertising',
    title: '节日促销氛围',
    icon: '🎄',
    tags: ['广告', '节日', '促销'],
    prompt:
      'Festive atmosphere with twinkling lights, gift boxes wrapped in colorful ribbons, snow gently falling, warm golden glow, cozy indoor setting, slow pan across the scene, holiday commercial aesthetic, joyful mood, 4K',
  },
  // === 教育科普类 ===
  {
    id: 'scene-13',
    category: 'education',
    title: '科学实验过程',
    icon: '🧪',
    tags: ['教育', '科学', '实验'],
    prompt:
      'A chemistry experiment in a modern lab, colorful liquid being poured into a beaker, bubbles forming and rising, smoke effects, macro close-up, educational video style, clear focus on the reaction, dramatic lighting, 4K',
  },
  {
    id: 'scene-14',
    category: 'education',
    title: '星空延时摄影',
    icon: '🌌',
    tags: ['教育', '天文', '延时'],
    prompt:
      'Time-lapse of a starry night sky with the Milky Way rotating above a desert landscape, stars trailing in circular patterns, moon rising, silhouetted mountains below, ultra-wide angle, astrophotography quality, 8K, 24fps',
  },
  // === 创意抽象类 ===
  {
    id: 'scene-15',
    category: 'abstract',
    title: '粒子流体艺术',
    icon: '🎨',
    tags: ['创意', '抽象', '粒子'],
    prompt:
      'Mesmerizing abstract art with colorful fluid particles flowing and swirling together, ink in water effect, neon colors against dark background, hypnotic smooth motion, digital art animation, macro detail, 8K render quality',
  },
];

/** 分类中文名映射 */
export const CATEGORY_LABEL: Record<PresetPrompt['category'], string> = {
  landscape: '风景',
  lifestyle: '生活',
  product: '产品',
  advertising: '广告',
  education: '教育',
  abstract: '创意',
};

/** 按分类顺序返回，保持展示确定性 */
export const CATEGORY_ORDER: PresetPrompt['category'][] = [
  'landscape',
  'lifestyle',
  'product',
  'advertising',
  'education',
  'abstract',
];

/** 按分类分组后的模板 */
export function groupedPresets(): Record<PresetPrompt['category'], PresetPrompt[]> {
  const result: Partial<Record<PresetPrompt['category'], PresetPrompt[]>> = {};
  for (const category of CATEGORY_ORDER) {
    result[category] = PRESET_PROMPTS.filter((p) => p.category === category);
  }
  return result as Record<PresetPrompt['category'], PresetPrompt[]>;
}