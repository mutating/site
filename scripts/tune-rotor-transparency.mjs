import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const sourcePath = path.resolve("public/pillar-testing-speed-rotor-v2.png");
const outputPath = path.resolve("public/pillar-testing-speed-rotor-v4.png");
const temporaryPath = `${outputPath}.prepared.png`;
const hotPink = [255, 44, 146];

const decoded = await sharp(sourcePath)
  .ensureAlpha()
  .raw()
  .toBuffer({ resolveWithObject: true });

for (let pixel = 0; pixel < decoded.info.width * decoded.info.height; pixel += 1) {
  const offset = pixel * decoded.info.channels;
  const alpha = decoded.data[offset + 3];

  if (alpha > 0 && alpha < 128) {
    const tint = 0.16;

    decoded.data[offset] = Math.round(
      decoded.data[offset] * (1 - tint) + hotPink[0] * tint,
    );
    decoded.data[offset + 1] = Math.round(
      decoded.data[offset + 1] * (1 - tint) + hotPink[1] * tint,
    );
    decoded.data[offset + 2] = Math.round(
      decoded.data[offset + 2] * (1 - tint) + hotPink[2] * tint,
    );
    decoded.data[offset + 3] = Math.max(1, Math.round(alpha * 0.35));
  } else if (alpha >= 128 && alpha < 224) {
    decoded.data[offset + 3] = Math.round(alpha * 0.7);
  }
}

await sharp(decoded.data, {
  raw: {
    width: decoded.info.width,
    height: decoded.info.height,
    channels: decoded.info.channels,
  },
})
  .png()
  .toFile(temporaryPath);

await fs.rename(temporaryPath, outputPath);
console.log(`Wrote ${path.relative(process.cwd(), outputPath)}.`);
