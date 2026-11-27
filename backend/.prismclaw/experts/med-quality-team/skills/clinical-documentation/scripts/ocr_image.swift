// Read the explicitly selected image with local macOS Vision; never correct medical text.
import Foundation
import Vision
import ImageIO
import CryptoKit

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "ImageOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Usage: swift ocr_image.swift <selected image>"])
    }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let bytes = try Data(contentsOf: url)
    guard bytes.count <= 35_000_000,
          let source = CGImageSourceCreateWithData(bytes as CFData, nil),
          let type = CGImageSourceGetType(source) as String?,
          ["public.jpeg", "public.png"].contains(type),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any] else {
        throw NSError(domain: "ImageOCR", code: 2, userInfo: [NSLocalizedDescriptionKey: "Unreadable image or image exceeds 35 MB"])
    }
    guard let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 20000, height <= 20000,
          width * height <= 40_000_000 else {
        throw NSError(domain: "ImageOCR", code: 3, userInfo: [NSLocalizedDescriptionKey: "Image dimensions exceed reading limits"])
    }
    let rawOrientation = (properties[kCGImagePropertyOrientation] as? UInt32) ?? 1
    let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) ?? .up
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.004
    let handler = VNImageRequestHandler(url: url, orientation: orientation, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted {
        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.008 {
            return $0.boundingBox.midY > $1.boundingBox.midY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let lines: [[String: Any]] = observations.enumerated().map { index, observation in
        let box = observation.boundingBox
        return ["line": index + 1,
                "boxTopLeftNormalized": [box.minX, 1 - box.maxY, box.width, box.height],
                "candidates": observation.topCandidates(3).map {
                    ["text": $0.string, "confidence": Double($0.confidence)] as [String: Any]
                }]
    }
    let payload: [String: Any] = [
        "schema": 1, "sourceFile": url.lastPathComponent,
        "sourceSha256": SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
        "pixelWidth": properties[kCGImagePropertyPixelWidth] ?? NSNull(),
        "pixelHeight": properties[kCGImagePropertyPixelHeight] ?? NSNull(),
        "languageCorrection": false,
        "scope": "本地OCR候选，不是确认转录；关键字段逐行回看原图和坐标，低置信度保留待核，不补造被遮挡文字。",
        "lines": lines
    ]
    let output = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(output)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data(("Image OCR failed: " + error.localizedDescription + "\n").utf8))
    exit(2)
}
