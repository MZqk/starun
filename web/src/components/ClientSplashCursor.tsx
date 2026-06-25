'use client';

import dynamic from 'next/dynamic';

const SplashCursor = dynamic(() => import('./SplashCursor'), { ssr: false });

export default SplashCursor;
