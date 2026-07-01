"use client";

import Galaxy from "./Galaxy";

export default function StarfieldBackground() {
  return (
    <>
      <div id="starfield" aria-hidden="true">
        <Galaxy
          transparent={true}
          focal={[0.5, 0.5]}
          rotation={[0.94, -0.34]}
          density={1.3}
          glowIntensity={0.35}
          saturation={0.7}
          hueShift={160}
          starSpeed={0.25}
          rotationSpeed={0.015}
          mouseInteraction={true}
          mouseRepulsion={true}
          repulsionStrength={2.2}
        />
        <div className="aura aura-1" aria-hidden="true" />
        <div className="aura aura-2" aria-hidden="true" />
        <div className="aura aura-3" aria-hidden="true" />
      </div>
    </>
  );
}
