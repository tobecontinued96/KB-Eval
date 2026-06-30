import type { Components } from "react-markdown";

// 把 react-markdown 默认节点映射成带 report-md-* className 的标签，
// 方便 styles.css 精准定位。table 外层再包一层 .report-md-table-wrap
// 用来给宽表做横向滚动。链接默认新 Tab 打开，避免 LangSmith 长链占满页面。

export const reportMarkdownComponents: Components = {
  h1: ({ node: _node, ...props }) => <h1 className="report-md-h1" {...props} />,
  h2: ({ node: _node, ...props }) => <h2 className="report-md-h2" {...props} />,
  h3: ({ node: _node, ...props }) => <h3 className="report-md-h3" {...props} />,
  p: ({ node: _node, ...props }) => <p className="report-md-p" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="report-md-ul" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="report-md-ol" {...props} />,
  li: ({ node: _node, ...props }) => <li className="report-md-li" {...props} />,
  a: ({ node: _node, ...props }) => (
    <a className="report-md-a" target="_blank" rel="noreferrer" {...props} />
  ),
  code: ({ node: _node, className, children, ...props }) => (
    <code
      className={className ? `report-md-code ${className}` : "report-md-code"}
      {...props}
    >
      {children}
    </code>
  ),
  table: ({ node: _node, ...props }) => (
    <div className="report-md-table-wrap">
      <table className="report-md-table" {...props} />
    </div>
  ),
  thead: ({ node: _node, ...props }) => <thead className="report-md-thead" {...props} />,
  tbody: ({ node: _node, ...props }) => <tbody className="report-md-tbody" {...props} />,
  tr: ({ node: _node, ...props }) => <tr className="report-md-tr" {...props} />,
  th: ({ node: _node, ...props }) => <th className="report-md-th" {...props} />,
  td: ({ node: _node, ...props }) => <td className="report-md-td" {...props} />,
  blockquote: ({ node: _node, ...props }) => (
    <blockquote className="report-md-blockquote" {...props} />
  ),
  hr: ({ node: _node, ...props }) => <hr className="report-md-hr" {...props} />
};