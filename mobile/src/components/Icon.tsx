import React from 'react';
import { Feather, MaterialCommunityIcons } from '@expo/vector-icons';

// The one place every icon in the app comes from. Feather covers nearly
// everything the design calls for (verified names below against the
// installed @expo/vector-icons glyph maps); MaterialCommunityIcons is
// used only for the handful Feather doesn't have (currently just
// "gauge", for playback speed). No emoji anywhere in the app should
// render as an icon -- this component is the replacement for all of them.
export type FeatherIconName =
  | 'home' | 'book-open' | 'search' | 'play' | 'pause' | 'clock' | 'user'
  | 'settings' | 'bell' | 'chevron-right' | 'chevron-left' | 'chevron-down'
  | 'lock' | 'check' | 'check-circle' | 'download' | 'heart'
  | 'more-horizontal' | 'arrow-left' | 'maximize' | 'minimize' | 'volume-2'
  | 'volume-x' | 'calendar' | 'credit-card' | 'log-out' | 'x'
  | 'alert-circle' | 'film' | 'users' | 'edit-2' | 'shield' | 'refresh-cw'
  | 'file-text' | 'award' | 'video' | 'alert-triangle' | 'user-x' | 'trash-2';

type MaterialIconName = 'gauge';

type IconProps = {
  name: FeatherIconName;
  size?: number;
  color?: string;
  style?: any;
};

export default function Icon({ name, size = 20, color = '#F4F4F5', style }: IconProps) {
  return <Feather name={name} size={size} color={color} style={style} />;
}

export function MaterialIcon({ name, size = 20, color = '#F4F4F5', style }: { name: MaterialIconName; size?: number; color?: string; style?: any }) {
  return <MaterialCommunityIcons name={name} size={size} color={color} style={style} />;
}
