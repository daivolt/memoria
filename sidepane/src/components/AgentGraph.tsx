import React, { useRef, useEffect, useMemo } from 'react';
import * as d3 from 'd3';
import { useDashboardStore } from '../store/dashboardStore';
import type { AgentNode } from '../types/dashboard';

const STATUS_COLORS: Record<string, string> = {
  active: '#34d399',
  idle: '#fbbf24',
  busy: '#818cf8',
  stale: '#f87171',
  unknown: '#94a3b8',
};

export function AgentGraph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const agents = useDashboardStore((s) => s.agents);
  const edges = useDashboardStore((s) => s.edges);

  const nodes = useMemo(
    () =>
      agents.map((a) => ({
        ...a,
        color: STATUS_COLORS[a.status] || STATUS_COLORS.unknown,
      })),
    [agents]
  );

  useEffect(() => {
    if (!svgRef.current || nodes.length === 0) return;
    const svg = d3.select(svgRef.current);
    const W = svgRef.current.clientWidth;
    const H = svgRef.current.clientHeight;
    if (W < 10 || H < 10) return;

    svg.selectAll('*').remove();
    const g = svg.append('g');

    const linkData = edges.filter(
      (e) => nodes.find((n) => n.name === e.source) && nodes.find((n) => n.name === e.target)
    );
    const nodeData = nodes as (AgentNode & { color: string })[];

    const sim = d3
      .forceSimulation(nodeData)
      .force(
        'link',
        d3.forceLink(linkData).id((d: any) => d.name).distance(100).strength(0.05)
      )
      .force('charge', d3.forceManyBody().strength(-250))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide(30))
      .alphaDecay(0.02);

    const link = g
      .selectAll('line')
      .data(linkData)
      .join('line')
      .attr('stroke', 'rgba(148,163,184,0.3)')
      .attr('stroke-width', 1.5);

    const node = g
      .selectAll<SVGGElement, AgentNode & { color: string }>('g.node')
      .data(nodeData, (d: any) => d.name)
      .join('g')
      .attr('class', 'node')
      .call(
        d3.drag<SVGGElement, any>()
          .on('start', (e, d) => {
            if (!e.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (e, d) => {
            d.fx = e.x;
            d.fy = e.y;
          })
          .on('end', (e, d) => {
            if (!e.active) sim.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    node
      .append('circle')
      .attr('r', 24)
      .attr('fill', (d) => d.color + '22')
      .attr('stroke', (d) => d.color)
      .attr('stroke-width', 2.5);

    node
      .append('circle')
      .attr('class', 'inner')
      .attr('r', 6)
      .attr('fill', (d) => d.color);

    node
      .append('text')
      .attr('dy', 14)
      .attr('text-anchor', 'middle')
      .attr('fill', (d) => d.color)
      .attr('font-size', '9px')
      .attr('font-family', 'monospace')
      .text((d) => d.name.slice(0, 7));

    sim.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y);
      node.attr('transform', (d) => `translate(${d.x},${d.y})`);
    });

    return () => {
      sim.stop();
    };
  }, [nodes, edges]);

  return (
    <svg
      ref={svgRef}
      style={{ width: '100%', height: '100%', background: 'radial-gradient(ellipse at center, #1a1a2e 0%, #0f0f1a 70%, #0a0a10 100%)' }}
    />
  );
}
