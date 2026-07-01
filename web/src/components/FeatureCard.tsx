import type { ReactNode } from "react";
import Link from "next/link";
import { ArrowIcon } from "./Icons";

export type FeatureCardVariant = "primary" | "secondary" | "horizontal";

type FeatureCardProps = {
  title: string;
  description: string;
  href: string;
  linkLabel: string;
  icon: ReactNode;
  variant: FeatureCardVariant;
};

export default function FeatureCard({
  title,
  description,
  href,
  linkLabel,
  icon,
  variant,
}: FeatureCardProps) {
  return (
    <article className={`feature-card feature-card--${variant}`}>
      <div className="border-mask" aria-hidden="true" />
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
          <Link className="feature-link" href={href}>
            {linkLabel}
            <ArrowIcon />
          </Link>
        </div>
      </div>
    </article>
  );
}
