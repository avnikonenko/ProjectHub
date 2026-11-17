import { Children, type ReactNode } from 'react';
import './feedback.css';

export function Feedback({ children, tone = 'info', className = '' }: {
  children?: ReactNode;
  tone?: 'info' | 'success' | 'error';
  className?: string;
}) {
  if (children === '' || Children.toArray(children).length === 0) return null;
  return <div className={`feedback feedback-${tone}${tone === 'error' ? ' error' : ''}${className ? ' ' + className : ''}`}
    role={tone === 'error' ? 'alert' : 'status'} aria-atomic={false}>
    {children}
  </div>;
}
