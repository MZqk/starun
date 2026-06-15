import type { ReactNode } from "react";
import Link from "next/link";
import { ArrowIcon } from "./Icons";

export type FeatureCardVariant = "primary" | "secondary" | "horizontal";
export type FeatureTagTone = "amber" | "sage" | "dusty";

type FeatureTag = {
  label: string;
  tone: FeatureTagTone;
};

type FeatureCardProps = {
  title: string;
  description: string;
  href: string;
  linkLabel: string;
  icon: ReactNode;
  tags: readonly FeatureTag[];
  variant: FeatureCardVariant;
};

export default function FeatureCard({
  title,
  description,
  href,
  linkLabel,
  icon,
  tags,
  variant,
}: FeatureCardProps) {
  return (
    <article className={`feature-card feature-card--${variant}`}>
      <div className="feature-card__decoration">
        <span className="feature-card__icon">{icon}</span>
        <span className="feature-card__line" />
      </div>
      <div className="feature-card__content">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <div className="feature-card__footer">
          <div className="feature-tags" aria-hidden="true">
            {tags.map((tag) => (
              <span className={`feature-tag feature-tag--${tag.tone}`} key={tag.label}>
                {tag.label}
              </span>
            ))}
          </div>
          <Link className="feature-link" href={href}>
            {linkLabel}
            <ArrowIcon />
          </Link>
        </div>
      </div>
    </article>
  );
}

