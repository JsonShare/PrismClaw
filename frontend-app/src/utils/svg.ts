const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/** 仅处理 ASCII 的 base64 编码（小程序无 btoa，图标与插画均为 ASCII SVG） */
export function base64Ascii(input: string): string {
  let out = ''
  for (let i = 0; i < input.length; i += 3) {
    const c1 = input.charCodeAt(i)
    const c2 = i + 1 < input.length ? input.charCodeAt(i + 1) : NaN
    const c3 = i + 2 < input.length ? input.charCodeAt(i + 2) : NaN
    const e1 = c1 >> 2
    const e2 = ((c1 & 3) << 4) | (Number.isNaN(c2) ? 0 : c2 >> 4)
    const e3 = Number.isNaN(c2) ? 64 : ((c2 & 15) << 2) | (Number.isNaN(c3) ? 0 : c3 >> 6)
    const e4 = Number.isNaN(c3) ? 64 : c3 & 63
    out += B64.charAt(e1) + B64.charAt(e2) + (e3 === 64 ? '=' : B64.charAt(e3)) + (e4 === 64 ? '=' : B64.charAt(e4))
  }
  return out
}

/** 生成 base64 SVG Data URI，供 <Image> 使用（小程序不支持内联 <svg>） */
export function svgDataUri(svg: string): string {
  return `data:image/svg+xml;base64,${base64Ascii(svg)}`
}
