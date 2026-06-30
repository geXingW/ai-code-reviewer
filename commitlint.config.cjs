module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'docs', 'style', 'refactor', 'perf', 'test', 'chore', 'build', 'ci', 'revert'],
    ],
    'subject-case': [0],
    'header-max-length': [2, 'always', 100],
    // Body and footer line length is often too restrictive for technical
    // commits (URLs, code refs, Chinese mixed text). Disable to avoid noise.
    'body-max-line-length': [0],
    'footer-max-line-length': [0],
  },
};
